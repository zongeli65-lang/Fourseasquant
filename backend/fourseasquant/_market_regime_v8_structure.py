"""第八版正式内核：用技术评分同口径的 EMA3 指数移动平均线极值结构判断市场状态。

本模块只包含纯计算，不读取数据库、不写文件。局部极值在 EMA3 的有效斜率
反向时才被确认，状态从确认日开始生效，避免未来信息回填。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


TrendState = Literal["rising", "sideways", "falling"]
ExtremumKind = Literal["maximum", "minimum"]


@dataclass(frozen=True)
class MarketBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class ConfirmedExtremum:
    kind: ExtremumKind
    occurred_on: date
    confirmed_on: date
    value: float
    atr10: float


@dataclass(frozen=True)
class StructureRegimeDay:
    trading_date: date
    state: TrendState
    ema3: float
    atr10: float
    derivative: float
    derivative_trend: int
    high_sequence_strength_atr: float
    low_sequence_strength_atr: float
    peak_lifts_atr: tuple[float, ...]
    trough_lifts_atr: tuple[float, ...]
    latest_maximum: ConfirmedExtremum | None
    latest_minimum: ConfirmedExtremum | None
    confirmed_extremum: ConfirmedExtremum | None
    reason: str


def _updated_segment_index(
    values: list[float], current: int, candidate: int, trend: int
) -> int:
    """与 technical_scoring._updated_segment_index 保持同一选择规则。"""
    if trend > 0 and values[candidate] >= values[current]:
        return candidate
    if trend < 0 and values[candidate] <= values[current]:
        return candidate
    return current


def _normalized_lifts(extrema: list[ConfirmedExtremum]) -> list[float]:
    """与技术评分相同：极值差除以相邻极值自身 ATR10 的较大值。"""
    return [
        (extrema[index].value - extrema[index - 1].value)
        / max(extrema[index].atr10, extrema[index - 1].atr10)
        for index in range(1, len(extrema))
    ]


def _sequence_strength(lifts: list[float]) -> float:
    """最近两次同向变化中的弱者；方向混合或样本不足时为零。"""
    if len(lifts) < 2:
        return 0.0
    recent = lifts[-2:]
    if all(value > 0 for value in recent):
        return min(recent)
    if all(value < 0 for value in recent):
        return max(recent)
    return 0.0


def _classify(
    peak_lifts: list[float],
    trough_lifts: list[float],
    *,
    effective_lift_atr: float,
) -> tuple[TrendState, float, float, str]:
    high_strength = _sequence_strength(peak_lifts)
    low_strength = _sequence_strength(trough_lifts)
    if (
        high_strength > effective_lift_atr
        and low_strength > effective_lift_atr
    ):
        return (
            "rising",
            high_strength,
            low_strength,
            "最近两次高点和低点均有效抬高",
        )
    if (
        high_strength < -effective_lift_atr
        and low_strength < -effective_lift_atr
    ):
        return (
            "falling",
            high_strength,
            low_strength,
            "最近两次高点和低点均有效降低",
        )
    return (
        "sideways",
        high_strength,
        low_strength,
        "极值不足、变化不足阈值或高低点方向不一致",
    )


def classify_market_structure(
    bars: list[MarketBar],
    *,
    ema_span: int = 3,
    atr_period: int = 10,
    zero_band_atr: float = 0.10,
    effective_lift_atr: float = 0.10,
) -> tuple[list[StructureRegimeDay], list[ConfirmedExtremum]]:
    """逐日返回结构状态；全部参数默认值与 technical-v3 技术评分一致。"""
    if not bars:
        return [], []

    alpha = 2 / (ema_span + 1)
    ema_values: list[float] = []
    atr_values: list[float] = []
    true_ranges: list[float] = []
    maxima: list[ConfirmedExtremum] = []
    minima: list[ConfirmedExtremum] = []
    all_extrema: list[ConfirmedExtremum] = []
    last_trend: int | None = None
    segment_index = 0
    result: list[StructureRegimeDay] = []

    for index, bar in enumerate(bars):
        ema = (
            bar.close
            if index == 0
            else alpha * bar.close + (1 - alpha) * ema_values[-1]
        )
        ema_values.append(ema)
        previous_close = bars[index - 1].close if index else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        true_ranges.append(true_range)
        atr_window = true_ranges[max(0, index - atr_period + 1) : index + 1]
        atr = max(sum(atr_window) / len(atr_window), bar.close * 0.0001)
        atr_values.append(atr)
        derivative = ema - ema_values[index - 1] if index else 0.0
        zero_threshold = atr * zero_band_atr
        trend = 1 if derivative > zero_threshold else -1 if derivative < -zero_threshold else 0

        confirmed: ConfirmedExtremum | None = None
        if trend != 0:
            if last_trend is None:
                last_trend = trend
                segment_index = index
            elif trend == last_trend:
                segment_index = _updated_segment_index(
                    ema_values, segment_index, index, trend
                )
            else:
                kind: ExtremumKind = "maximum" if last_trend > 0 else "minimum"
                confirmed = ConfirmedExtremum(
                    kind=kind,
                    occurred_on=bars[segment_index].trading_date,
                    confirmed_on=bar.trading_date,
                    value=ema_values[segment_index],
                    atr10=atr_values[segment_index],
                )
                (maxima if kind == "maximum" else minima).append(confirmed)
                all_extrema.append(confirmed)
                last_trend = trend
                segment_index = index
        elif last_trend is not None:
            segment_index = _updated_segment_index(
                ema_values, segment_index, index, last_trend
            )

        peak_lifts = _normalized_lifts(maxima)
        trough_lifts = _normalized_lifts(minima)
        state, high_strength, low_strength, reason = _classify(
            peak_lifts,
            trough_lifts,
            effective_lift_atr=effective_lift_atr,
        )
        result.append(
            StructureRegimeDay(
                trading_date=bar.trading_date,
                state=state,
                ema3=ema,
                atr10=atr,
                derivative=derivative,
                derivative_trend=trend,
                high_sequence_strength_atr=high_strength,
                low_sequence_strength_atr=low_strength,
                peak_lifts_atr=tuple(peak_lifts[-2:]),
                trough_lifts_atr=tuple(trough_lifts[-2:]),
                latest_maximum=maxima[-1] if maxima else None,
                latest_minimum=minima[-1] if minima else None,
                confirmed_extremum=confirmed,
                reason=reason,
            )
        )

    return result, all_extrema
