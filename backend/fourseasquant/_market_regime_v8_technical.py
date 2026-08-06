"""第八版正式内核：把结构、突破、相对强度和成交量映射为市场状态。

个股相对强度原本相对市场基准计算；市场本身没有更高一级的同类基准，因此
本原型把它映射为五个主要指数在 3/5/10 日窗口内的同步方向强度。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fourseasquant._market_regime_v8_structure import (
    MarketBar,
    StructureRegimeDay,
    TrendState,
    classify_market_structure,
)


Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class MarketTechnicalRegimeDay:
    trading_date: date
    state: TrendState
    base_structure_state: TrendState
    fast_override: Direction | None
    bullish_trigger: bool
    bearish_trigger: bool
    bullish_fast_score: float
    bearish_fast_score: float
    relative_strength: float
    volume_confirmation: float
    bullish_breakout_count: int
    bearish_breakout_count: int
    bullish_breakout_symbols: tuple[str, ...]
    bearish_breakout_symbols: tuple[str, ...]
    bullish_breakout_strength: float
    bearish_breakout_strength: float
    trigger_reason: str
    sh_structure: StructureRegimeDay


def _scaled_directional_return(return_pct: float, scale_pct: float) -> float:
    """技术评分缩放公式的有符号形式，结果位于 -1 到 +1。"""
    return return_pct / (abs(return_pct) + scale_pct)


def _relative_strength(
    closes: dict[str, list[float]],
    index: int,
) -> float:
    values: list[float] = []
    for series in closes.values():
        horizon_values: list[float] = []
        for horizon, scale in ((3, 5.0), (5, 8.0), (10, 12.0)):
            if index < horizon:
                continue
            return_pct = (series[index] / series[index - horizon] - 1) * 100
            horizon_values.append(_scaled_directional_return(return_pct, scale))
        values.append(sum(horizon_values) / len(horizon_values) if horizon_values else 0.0)
    return sum(values) / len(values)


def _volume_confirmation(
    volumes: dict[str, list[int]],
    index: int,
) -> float:
    values: list[float] = []
    for series in volumes.values():
        previous = series[max(0, index - 10) : index]
        if not previous:
            values.append(0.0)
            continue
        average = sum(previous) / len(previous)
        ratio = series[index] / average if average > 0 else 0.0
        values.append(min(max(ratio / 2, 0.0), 1.0))
    return sum(values) / len(values)


def classify_market_technical_regime(
    index_bars: dict[str, list[MarketBar]],
    *,
    sh_symbol: str,
    ema_span: int = 3,
    atr_period: int = 10,
    zero_band_atr: float = 0.10,
    effective_lift_atr: float = 0.10,
    fast_memory_days: int = 5,
) -> list[MarketTechnicalRegimeDay]:
    """计算逐日状态；快速突破共振优先于慢速结构，效力保留五个交易日。"""
    if sh_symbol not in index_bars:
        raise ValueError("缺少上证指数")
    if len(index_bars) < 3:
        raise ValueError("至少需要三个指数形成市场同步强度")

    dates = [item.trading_date for item in index_bars[sh_symbol]]
    if any([item.trading_date for item in bars] != dates for bars in index_bars.values()):
        raise ValueError("主要指数交易日不完全一致")

    structure_by_symbol: dict[str, list[StructureRegimeDay]] = {}
    for symbol, bars in index_bars.items():
        structure_by_symbol[symbol], _ = classify_market_structure(
            bars,
            ema_span=ema_span,
            atr_period=atr_period,
            zero_band_atr=zero_band_atr,
            effective_lift_atr=effective_lift_atr,
        )

    closes = {
        symbol: [item.close for item in bars]
        for symbol, bars in index_bars.items()
    }
    volumes = {
        symbol: [item.volume for item in bars]
        for symbol, bars in index_bars.items()
    }
    previous_ema: dict[str, float | None] = {symbol: None for symbol in index_bars}
    bullish_streak = {symbol: 0 for symbol in index_bars}
    bearish_streak = {symbol: 0 for symbol in index_bars}
    active_direction: Direction | None = None
    active_until = -1
    result: list[MarketTechnicalRegimeDay] = []
    index_count = len(index_bars)

    for index, trading_date in enumerate(dates):
        bullish_strength_sum = 0.0
        bearish_strength_sum = 0.0
        bullish_count = 0
        bearish_count = 0
        bullish_symbols: list[str] = []
        bearish_symbols: list[str] = []
        for symbol, days in structure_by_symbol.items():
            item = days[index]
            latest_peak = item.latest_maximum.value if item.latest_maximum else None
            latest_trough = item.latest_minimum.value if item.latest_minimum else None
            bullish = (
                latest_peak is not None
                and item.derivative_trend > 0
                and item.ema3 > latest_peak + item.atr10 * zero_band_atr
            )
            bearish = (
                latest_trough is not None
                and item.derivative_trend < 0
                and item.ema3 < latest_trough - item.atr10 * zero_band_atr
            )
            prior_ema = previous_ema[symbol]
            if bullish:
                assert latest_peak is not None
                bullish_streak[symbol] = (
                    bullish_streak[symbol] + 1
                    if prior_ema is None or item.ema3 >= prior_ema
                    else 0
                )
                distance = (item.ema3 - latest_peak) / item.atr10
                bullish_strength_sum += (
                    0.75 * min(max(distance, 0.0), 3.0) / 3.0
                    + 0.25 * min(bullish_streak[symbol], 5) / 5
                )
                bullish_count += 1
                bullish_symbols.append(symbol)
            else:
                bullish_streak[symbol] = 0
            if bearish:
                assert latest_trough is not None
                bearish_streak[symbol] = (
                    bearish_streak[symbol] + 1
                    if prior_ema is None or item.ema3 <= prior_ema
                    else 0
                )
                distance = (latest_trough - item.ema3) / item.atr10
                bearish_strength_sum += (
                    0.75 * min(max(distance, 0.0), 3.0) / 3.0
                    + 0.25 * min(bearish_streak[symbol], 5) / 5
                )
                bearish_count += 1
                bearish_symbols.append(symbol)
            else:
                bearish_streak[symbol] = 0
            previous_ema[symbol] = item.ema3

        relative_strength = _relative_strength(closes, index)
        volume_confirmation = _volume_confirmation(volumes, index)
        bullish_breakout_strength = bullish_strength_sum / index_count
        bearish_breakout_strength = bearish_strength_sum / index_count
        bullish_fast_score = (
            20 * bullish_breakout_strength
            + 15 * max(relative_strength, 0.0)
            + 10 * volume_confirmation * bullish_count / index_count
        )
        bearish_fast_score = (
            20 * bearish_breakout_strength
            + 15 * max(-relative_strength, 0.0)
            + 10 * volume_confirmation * bearish_count / index_count
        )

        bullish_trigger = (
            bullish_count >= 1
            and relative_strength >= 0.30
            and bullish_fast_score >= 6.0
        ) or (
            bullish_count >= 3
            and relative_strength >= 0.20
            and bullish_fast_score >= 10.0
        )
        bearish_trigger = (
            bearish_count >= 1
            and relative_strength <= -0.30
            and bearish_fast_score >= 6.0
        ) or (
            bearish_count >= 3
            and relative_strength <= -0.20
            and bearish_fast_score >= 10.0
        )

        if bullish_trigger or bearish_trigger:
            if bullish_trigger and bearish_trigger:
                active_direction = (
                    "bullish"
                    if bullish_fast_score >= bearish_fast_score
                    else "bearish"
                )
            else:
                active_direction = "bullish" if bullish_trigger else "bearish"
            active_until = index + fast_memory_days - 1
        elif index > active_until:
            active_direction = None

        sh_structure = structure_by_symbol[sh_symbol][index]
        state: TrendState = (
            "rising"
            if active_direction == "bullish"
            else "falling"
            if active_direction == "bearish"
            else sh_structure.state
        )
        if bullish_trigger:
            reason = "上行突破与多指数相对强度共振"
        elif bearish_trigger:
            reason = "下行破位与多指数相对弱势共振"
        elif active_direction is not None:
            reason = f"快速{('上行' if active_direction == 'bullish' else '下行')}证据处于五日效力期"
        else:
            reason = f"无快速共振，采用上证结构状态：{sh_structure.state}"
        result.append(
            MarketTechnicalRegimeDay(
                trading_date=trading_date,
                state=state,
                base_structure_state=sh_structure.state,
                fast_override=active_direction,
                bullish_trigger=bullish_trigger,
                bearish_trigger=bearish_trigger,
                bullish_fast_score=bullish_fast_score,
                bearish_fast_score=bearish_fast_score,
                relative_strength=relative_strength,
                volume_confirmation=volume_confirmation,
                bullish_breakout_count=bullish_count,
                bearish_breakout_count=bearish_count,
                bullish_breakout_symbols=tuple(bullish_symbols),
                bearish_breakout_symbols=tuple(bearish_symbols),
                bullish_breakout_strength=bullish_breakout_strength,
                bearish_breakout_strength=bearish_breakout_strength,
                trigger_reason=reason,
                sh_structure=sh_structure,
            )
        )
    return result
