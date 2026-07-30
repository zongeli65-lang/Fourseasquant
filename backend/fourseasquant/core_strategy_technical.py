from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Literal, Self, cast

from pydantic import BaseModel, Field, model_validator

from fourseasquant.technical_scoring import DerivativeState


TECHNICAL_SIGNAL_VERSION = "core-technical-v4"
PatternDirection = Literal["bullish", "bearish", "neutral"]
PatternFunction = Literal["reversal", "continuation", "neutral"]
PatternStrength = Literal["strong", "weak", "neutral"]
PatternName = Literal[
    "doji",
    "hammer",
    "hanging_man",
    "shooting_star",
    "inverted_hammer",
    "bullish_engulfing",
    "bearish_engulfing",
    "piercing",
    "dark_cloud_cover",
    "harami",
    "doji_harami",
    "bullish_harami_confirmed",
    "bearish_harami_confirmed",
    "tweezer_bottom",
    "tweezer_top",
    "bullish_belt_hold",
    "bearish_belt_hold",
    "morning_star",
    "evening_star",
    "doji_morning_star",
    "doji_evening_star",
    "three_white_soldiers",
    "three_black_crows",
    "rising_three_methods",
    "falling_three_methods",
    "high_price_gapping_play",
    "low_price_gapping_play",
    "bullish_separating_lines",
    "bearish_separating_lines",
    "up_window",
    "down_window",
]
LevelRole = Literal["support", "resistance"]
LevelSource = Literal[
    "swing_low",
    "swing_high",
    "up_window",
    "down_window",
    "consolidation",
    "ascending_trendline",
    "descending_trendline",
    "failed_breakout_low",
    "failed_breakout_high",
    "polarity_conversion",
]
DivergenceDirection = Literal["bullish", "bearish"]
DivergenceStrength = Literal["weak", "standard"]
DivergenceStatus = Literal["candidate", "confirmed"]
DivergenceConfirmation = Literal["none", "candlestick", "rsi"]
AnalysisStatus = Literal[
    "ready",
    "insufficient_history",
    "corporate_actions_incomplete",
]
EntryTrigger = Literal[
    "support_retest",
    "strong_bullish_candlestick",
    "standard_rsi_bottom_divergence",
    "confirmed_bullish_composite",
    "shrinking_volume_pullback_restart",
]


class DailyTechnicalBar(BaseModel):
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    turnover_cny: int = Field(ge=0)
    derivative_state: DerivativeState
    suspended: bool = False
    corporate_action: bool = False

    @model_validator(mode="after")
    def validate_prices(self) -> Self:
        if self.high < max(self.open, self.close):
            raise ValueError("最高价不能低于开盘价或收盘价")
        if self.low > min(self.open, self.close):
            raise ValueError("最低价不能高于开盘价或收盘价")
        return self


class DailyTechnicalInput(BaseModel):
    """日线技术证据模块的唯一输入。"""

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    actual_date: date
    qfq_source: str = Field(min_length=1)
    listing_trading_days: int = Field(ge=0)
    corporate_actions_complete: bool
    bars: list[DailyTechnicalBar]

    @model_validator(mode="after")
    def validate_bars(self) -> Self:
        dates = [bar.date for bar in self.bars]
        if dates != sorted(dates):
            raise ValueError("前复权日线必须按日期升序排列")
        if len(set(dates)) != len(dates):
            raise ValueError("前复权日线日期不能重复")
        if self.bars and self.bars[-1].date != self.actual_date:
            raise ValueError("最后一根日线必须对应实际数据日期")
        return self


class SwingPoint(BaseModel):
    direction: Literal["high", "low"]
    occurred_on: date
    knowable_on: date
    price: float
    rsi14: float | None
    prominence_mr20: float


class CandlestickSignal(BaseModel):
    pattern: PatternName
    direction: PatternDirection
    function: PatternFunction
    strength: PatternStrength
    started_on: date
    completed_on: date
    low: float
    high: float
    context_derivative_state: DerivativeState


class RsiDivergenceSignal(BaseModel):
    direction: DivergenceDirection
    strength: DivergenceStrength
    status: DivergenceStatus
    historical_pivot_on: date
    formed_on: date
    confirmed_on: date | None
    historical_price: float
    current_price: float
    historical_rsi: float
    current_rsi: float
    rsi_difference: float
    confirmation: DivergenceConfirmation


class TechnicalLevel(BaseModel):
    role: LevelRole
    lower: float
    center: float
    upper: float
    formed_on: date
    sources: list[LevelSource]
    source_count: int = Field(ge=1)
    slope_per_trading_day: float = 0


class DailyTechnicalAnalysis(BaseModel):
    code: str
    name: str
    actual_date: date
    qfq_source: str
    rules_version: str = TECHNICAL_SIGNAL_VERSION
    status: AnalysisStatus
    rsi14: float | None = None
    mr20: float | None = None
    mb20: float | None = None
    volume_ratio: float | None = None
    turnover_ratio: float | None = None
    swings: list[SwingPoint] = Field(default_factory=list)
    candlesticks: list[CandlestickSignal] = Field(default_factory=list)
    rsi_divergences: list[RsiDivergenceSignal] = Field(default_factory=list)
    supports: list[TechnicalLevel] = Field(default_factory=list)
    resistances: list[TechnicalLevel] = Field(default_factory=list)
    entry_triggers: list[EntryTrigger] = Field(default_factory=list)
    strong_evidence_ids: list[str] = Field(default_factory=list)
    strong_bearish_veto: bool = False
    weak_bearish_rsi_composite_veto: bool = False
    standard_rsi_top_divergence_active: bool = False
    strong_bullish_continuation: bool = False
    big_bullish_candle: bool = False
    valid_volume_breakout: bool = False
    confirmed_pressure_breakout: bool = False
    stop_price: float | None = None
    pressure_target: float | None = None
    gross_reward_risk_ratio: float | None = None
    reasons: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _Metrics:
    body: float
    range: float
    upper_shadow: float
    lower_shadow: float
    close_location: float
    mb20: float | None
    mr20: float | None
    mv20: float | None
    ma20: float | None
    volume_ratio: float | None
    turnover_ratio: float | None


@dataclass(frozen=True)
class _IndexedSwing:
    index: int
    value: SwingPoint


@dataclass(frozen=True)
class _IndexedPattern:
    start_index: int
    end_index: int
    value: CandlestickSignal


@dataclass
class _LevelCandidate:
    role: LevelRole
    lower: float
    center: float
    upper: float
    formed_index: int
    formed_on: date
    sources: list[LevelSource]
    slope_per_trading_day: float = 0


@dataclass(frozen=True)
class _CompositeEvidence:
    direction: DivergenceDirection
    confirmed_index: int
    evidence_id: str
    components: frozenset[str]


def analyze_daily_technical(
    source: DailyTechnicalInput,
) -> DailyTechnicalAnalysis:
    """
    将前复权日线和技术导数状态转换为标准化交易证据。

    该接口不读取数据库、不决定候选等级、不生成订单。
    它只回答当前收盘时已经可知的蜡烛图、RSI、
    支撑压力和分歧买点事实。
    """

    if not source.corporate_actions_complete:
        return _empty_analysis(source, "corporate_actions_incomplete")
    valid_bars = [
        bar
        for bar in source.bars
        if not bar.suspended
    ]
    if source.listing_trading_days < 60 or len(valid_bars) < 60:
        return _empty_analysis(source, "insufficient_history")

    metrics = _rolling_metrics(valid_bars)
    rsi = _wilder_rsi(valid_bars)
    swings = _confirmed_swings(valid_bars, metrics, rsi)
    patterns = _candlestick_patterns(valid_bars, metrics)
    divergences = _rsi_divergences(
        valid_bars,
        metrics,
        rsi,
        swings,
        patterns,
    )
    supports, resistances = _technical_levels(
        valid_bars,
        metrics,
        swings,
    )
    current_index = len(valid_bars) - 1
    current_metrics = metrics[current_index]
    assert current_metrics.mr20 is not None
    current_patterns = [
        item.value
        for item in patterns
        if item.end_index == current_index
    ]
    active_patterns = [
        item
        for item in patterns
        if current_index - item.end_index <= 5
    ]
    active_divergences = [
        item
        for item in divergences
        if item.confirmed_on is not None
        and _date_index(valid_bars, item.confirmed_on) is not None
        and current_index
        - cast_int(_date_index(valid_bars, item.confirmed_on))
        <= 5
    ]
    composites = _confirmed_composites(
        valid_bars,
        patterns=patterns,
        divergences=divergences,
        current_index=current_index,
    )

    entry_triggers: list[EntryTrigger] = []
    if _support_retest(valid_bars, metrics, supports):
        entry_triggers.append("support_retest")
    if any(
        pattern.direction == "bullish"
        and pattern.strength == "strong"
        for pattern in current_patterns
    ):
        entry_triggers.append("strong_bullish_candlestick")
    if any(
        divergence.direction == "bullish"
        and divergence.strength == "standard"
        and divergence.status == "confirmed"
        and divergence.confirmed_on == source.actual_date
        for divergence in divergences
    ):
        entry_triggers.append("standard_rsi_bottom_divergence")
    if any(
        item.direction == "bullish"
        and item.confirmed_index == current_index
        for item in composites
    ):
        entry_triggers.append("confirmed_bullish_composite")
    if _shrinking_pullback_restart(valid_bars, metrics, supports):
        entry_triggers.append("shrinking_volume_pullback_restart")

    latest_opposite_bearish = max(
        (
            item.end_index
            for item in active_patterns
            if item.value.direction == "bearish"
            and item.value.strength == "strong"
        ),
        default=-1,
    )
    strong_evidence_ids = [
        f"candlestick:{item.value.pattern}:{item.value.completed_on.isoformat()}"
        for item in active_patterns
        if item.value.direction == "bullish"
        and item.value.strength == "strong"
        and item.end_index > latest_opposite_bearish
    ]
    strong_evidence_ids.extend(
        f"rsi:bullish:{item.confirmed_on.isoformat()}"
        for item in active_divergences
        if item.direction == "bullish"
        and item.strength == "standard"
        and item.confirmed_on is not None
    )
    strong_evidence_ids.extend(
        item.evidence_id
        for item in composites
        if item.direction == "bullish"
        and current_index - item.confirmed_index <= 5
    )
    strong_evidence_ids = list(dict.fromkeys(strong_evidence_ids))
    strong_bearish_veto = any(
        item.value.direction == "bearish"
        and item.value.strength == "strong"
        for item in active_patterns
    )
    weak_bearish_rsi_composite_veto = any(
        item.direction == "bearish"
        and current_index - item.confirmed_index <= 5
        for item in composites
    )
    strong_bearish_veto = (
        strong_bearish_veto
        or weak_bearish_rsi_composite_veto
    )
    top_divergence_active = any(
        item.direction == "bearish"
        and item.strength == "standard"
        for item in active_divergences
    )
    strong_bullish_continuation = any(
        pattern.direction == "bullish"
        and pattern.function == "continuation"
        and pattern.strength == "strong"
        for pattern in current_patterns
    )
    big_bullish_candle = (
        valid_bars[-1].close > valid_bars[-1].open
        and _is_long(valid_bars[-1], current_metrics)
    )
    broken_resistances = _broken_resistances(
        valid_bars,
        metrics,
        resistances,
    )
    valid_volume_breakout = any(
        _volume_breaks_resistance(
            resistance,
            current_close=valid_bars[-1].close,
            metric=current_metrics,
        )
        for resistance in broken_resistances
    )
    confirmed_pressure_breakout = bool(broken_resistances)
    for broken_resistance in broken_resistances:
        resistances.remove(broken_resistance)
        converted_sources = list(
            dict.fromkeys(
                (
                    *broken_resistance.sources,
                    "polarity_conversion",
                )
            )
        )
        supports.append(
            broken_resistance.model_copy(
                update={
                    "role": "support",
                    "formed_on": source.actual_date,
                    "sources": converted_sources,
                    "source_count": len(converted_sources),
                }
            )
        )
    supports.sort(
        key=lambda level: _support_sort_key(
            level,
            current_close=valid_bars[-1].close,
        )
    )
    stop_price = (
        supports[0].lower - 0.10 * current_metrics.mr20
        if supports
        else None
    )
    pressure_target = _pressure_target(
        current_close=valid_bars[-1].close,
        resistances=resistances,
    )
    reward_risk = _reward_risk(
        entry=valid_bars[-1].close,
        stop=stop_price,
        target=pressure_target,
    )
    reasons: list[str] = []
    if not supports:
        reasons.append("no_active_support")
    if not resistances:
        reasons.append("no_active_resistance_all_time_high")
    elif pressure_target is None:
        reasons.append("active_resistance_not_cleared")

    return DailyTechnicalAnalysis(
        code=source.code,
        name=source.name,
        actual_date=source.actual_date,
        qfq_source=source.qfq_source,
        status="ready",
        rsi14=rsi[-1],
        mr20=current_metrics.mr20,
        mb20=current_metrics.mb20,
        volume_ratio=current_metrics.volume_ratio,
        turnover_ratio=current_metrics.turnover_ratio,
        swings=[item.value for item in swings[-20:]],
        candlesticks=[item.value for item in active_patterns],
        rsi_divergences=divergences[-20:],
        supports=supports,
        resistances=resistances,
        entry_triggers=list(dict.fromkeys(entry_triggers)),
        strong_evidence_ids=strong_evidence_ids,
        strong_bearish_veto=strong_bearish_veto,
        weak_bearish_rsi_composite_veto=(
            weak_bearish_rsi_composite_veto
        ),
        standard_rsi_top_divergence_active=top_divergence_active,
        strong_bullish_continuation=strong_bullish_continuation,
        big_bullish_candle=big_bullish_candle,
        valid_volume_breakout=valid_volume_breakout,
        confirmed_pressure_breakout=confirmed_pressure_breakout,
        stop_price=stop_price,
        pressure_target=pressure_target,
        gross_reward_risk_ratio=reward_risk,
        reasons=reasons,
    )


def _empty_analysis(
    source: DailyTechnicalInput,
    status: AnalysisStatus,
) -> DailyTechnicalAnalysis:
    return DailyTechnicalAnalysis(
        code=source.code,
        name=source.name,
        actual_date=source.actual_date,
        qfq_source=source.qfq_source,
        status=status,
    )


def _rolling_metrics(
    bars: list[DailyTechnicalBar],
) -> list[_Metrics]:
    result: list[_Metrics] = []
    for index, bar in enumerate(bars):
        body = abs(bar.close - bar.open)
        full_range = max(bar.high - bar.low, bar.close * 0.0001)
        previous = bars[max(0, index - 20) : index]
        mb20 = (
            statistics.median(
                abs(item.close - item.open)
                for item in previous
            )
            if len(previous) >= 20
            else None
        )
        mr20 = (
            statistics.median(item.high - item.low for item in previous)
            if len(previous) >= 20
            else None
        )
        mv20 = (
            statistics.median(item.volume for item in previous)
            if len(previous) >= 20
            else None
        )
        ma20 = (
            statistics.median(item.turnover_cny for item in previous)
            if len(previous) >= 20
            else None
        )
        result.append(
            _Metrics(
                body=body,
                range=full_range,
                upper_shadow=bar.high - max(bar.open, bar.close),
                lower_shadow=min(bar.open, bar.close) - bar.low,
                close_location=(bar.close - bar.low) / full_range,
                mb20=mb20,
                mr20=mr20,
                mv20=mv20,
                ma20=ma20,
                volume_ratio=(
                    bar.volume / max(mv20, 1)
                    if mv20 is not None
                    else None
                ),
                turnover_ratio=(
                    bar.turnover_cny / max(ma20, 1)
                    if ma20 is not None
                    else None
                ),
            )
        )
    return result


def _wilder_rsi(
    bars: list[DailyTechnicalBar],
    period: int = 14,
) -> list[float | None]:
    values: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return values
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(bars)):
        movement = bars[index].close - bars[index - 1].close
        gains.append(max(movement, 0))
        losses.append(max(-movement, 0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    values[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(bars)):
        average_gain = (
            average_gain * (period - 1) + gains[index - 1]
        ) / period
        average_loss = (
            average_loss * (period - 1) + losses[index - 1]
        ) / period
        values[index] = _rsi_value(average_gain, average_loss)
    return values


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0 and average_loss == 0:
        return 50
    if average_loss == 0:
        return 100
    return 100 - 100 / (1 + average_gain / average_loss)


def _confirmed_swings(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    rsi: list[float | None],
) -> list[_IndexedSwing]:
    swings: list[_IndexedSwing] = []
    for index in range(3, len(bars) - 3):
        mr20 = metrics[index].mr20
        if mr20 is None or mr20 <= 0:
            continue
        left = bars[index - 3 : index]
        right = bars[index + 1 : index + 4]
        bar = bars[index]
        low_candidate = (
            bar.low <= min(item.low for item in left)
            and bar.low < min(item.low for item in right)
        )
        high_candidate = (
            bar.high >= max(item.high for item in left)
            and bar.high > max(item.high for item in right)
        )
        if low_candidate:
            prominence = (
                min(
                    max(item.high for item in left),
                    max(item.high for item in right),
                )
                - bar.low
            ) / mr20
            if prominence >= 0.5:
                swings.append(
                    _IndexedSwing(
                        index=index,
                        value=SwingPoint(
                            direction="low",
                            occurred_on=bar.date,
                            knowable_on=bars[index + 3].date,
                            price=bar.low,
                            rsi14=rsi[index],
                            prominence_mr20=prominence,
                        ),
                    )
                )
        if high_candidate:
            prominence = (
                bar.high
                - max(
                    min(item.low for item in left),
                    min(item.low for item in right),
                )
            ) / mr20
            if prominence >= 0.5:
                swings.append(
                    _IndexedSwing(
                        index=index,
                        value=SwingPoint(
                            direction="high",
                            occurred_on=bar.date,
                            knowable_on=bars[index + 3].date,
                            price=bar.high,
                            rsi14=rsi[index],
                            prominence_mr20=prominence,
                        ),
                    )
                )
    swings.sort(key=lambda item: (item.index, item.value.direction))
    return swings


def _candlestick_patterns(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
) -> list[_IndexedPattern]:
    result: list[_IndexedPattern] = []
    for index in range(20, len(bars)):
        result.extend(_single_bar_patterns(bars, metrics, index))
        result.extend(_two_bar_patterns(bars, metrics, index))
        result.extend(_three_bar_patterns(bars, metrics, index))
        methods = _three_methods(bars, metrics, index)
        if methods is not None:
            result.append(methods)
        gapping_play = _gapping_play(bars, metrics, index)
        if gapping_play is not None:
            result.append(gapping_play)
        window = _window_pattern(bars, metrics, index)
        if window is not None:
            result.append(window)
    return _deduplicate_patterns(result)


def _single_bar_patterns(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> list[_IndexedPattern]:
    bar = bars[index]
    metric = metrics[index]
    if metric.mb20 is None or metric.mr20 is None:
        return []
    trend = bars[index - 1].derivative_state
    values: list[_IndexedPattern] = []
    if _is_doji(metric):
        values.append(
            _pattern(
                bars,
                index,
                index,
                "doji",
                "neutral",
                "neutral",
                "neutral",
                trend,
            )
        )
    lower_rejection = (
        metric.lower_shadow
        >= 2 * max(metric.body, bar.close * 0.0001)
        and metric.upper_shadow <= 0.10 * metric.range
        and metric.body / metric.range <= 0.35
    )
    upper_rejection = (
        metric.upper_shadow
        >= 2 * max(metric.body, bar.close * 0.0001)
        and metric.lower_shadow <= 0.10 * metric.range
        and metric.body / metric.range <= 0.35
    )
    if lower_rejection and trend == "negative":
        values.append(
            _pattern(
                bars,
                index,
                index,
                "hammer",
                "bullish",
                "reversal",
                "weak",
                trend,
            )
        )
    elif lower_rejection and trend == "positive":
        values.append(
            _pattern(
                bars,
                index,
                index,
                "hanging_man",
                "bearish",
                "reversal",
                "weak",
                trend,
            )
        )
    if upper_rejection and trend == "positive":
        values.append(
            _pattern(
                bars,
                index,
                index,
                "shooting_star",
                "bearish",
                "reversal",
                "weak",
                trend,
            )
        )
    elif upper_rejection and trend == "negative":
        values.append(
            _pattern(
                bars,
                index,
                index,
                "inverted_hammer",
                "bullish",
                "reversal",
                "weak",
                trend,
            )
        )
    bullish_belt_hold = (
        trend == "negative"
        and bar.close > bar.open
        and _is_long(bar, metric)
        and bar.open - bar.low <= 0.05 * metric.range
        and metric.close_location >= 0.80
    )
    bearish_belt_hold = (
        trend == "positive"
        and bar.close < bar.open
        and _is_long(bar, metric)
        and bar.high - bar.open <= 0.05 * metric.range
        and metric.close_location <= 0.20
    )
    if bullish_belt_hold:
        values.append(
            _pattern(
                bars,
                index,
                index,
                "bullish_belt_hold",
                "bullish",
                "reversal",
                "weak",
                trend,
            )
        )
    if bearish_belt_hold:
        values.append(
            _pattern(
                bars,
                index,
                index,
                "bearish_belt_hold",
                "bearish",
                "reversal",
                "weak",
                trend,
            )
        )
    return values


def _two_bar_patterns(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> list[_IndexedPattern]:
    if index < 1 or metrics[index].mr20 is None:
        return []
    first = bars[index - 1]
    second = bars[index]
    first_metric = metrics[index - 1]
    second_metric = metrics[index]
    trend = bars[index - 2].derivative_state if index >= 2 else "zero"
    tolerance = 0.05 * cast_float(metrics[index].mr20)
    values: list[_IndexedPattern] = []
    bullish_engulfing = (
        trend == "negative"
        and first.close < first.open
        and second.close > second.open
        and min(second.open, second.close)
        <= min(first.open, first.close) + tolerance
        and max(second.open, second.close)
        >= max(first.open, first.close) - tolerance
    )
    bearish_engulfing = (
        trend == "positive"
        and first.close > first.open
        and second.close < second.open
        and min(second.open, second.close)
        <= min(first.open, first.close) + tolerance
        and max(second.open, second.close)
        >= max(first.open, first.close) - tolerance
    )
    if bullish_engulfing:
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "bullish_engulfing",
                "bullish",
                "reversal",
                "strong",
                trend,
            )
        )
    elif (
        trend == "negative"
        and _is_long(first, first_metric)
        and first.close < first.open
        and second.close > second.open
        and second.open < first.close
        and second.close > (first.open + first.close) / 2
        and second.close < first.open
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "piercing",
                "bullish",
                "reversal",
                "strong",
                trend,
            )
        )
    if bearish_engulfing:
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "bearish_engulfing",
                "bearish",
                "reversal",
                "strong",
                trend,
            )
        )
    elif (
        trend == "positive"
        and _is_long(first, first_metric)
        and first.close > first.open
        and second.close < second.open
        and second.open > first.close
        and second.close < (first.open + first.close) / 2
        and second.close > first.open
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "dark_cloud_cover",
                "bearish",
                "reversal",
                "strong",
                trend,
            )
        )
    first_lower = min(first.open, first.close)
    first_upper = max(first.open, first.close)
    second_lower = min(second.open, second.close)
    second_upper = max(second.open, second.close)
    harami = (
        _is_long(first, first_metric)
        and _is_small(second_metric)
        and second_lower >= first_lower - tolerance
        and second_upper <= first_upper + tolerance
    )
    if harami:
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "doji_harami"
                if _is_doji(second_metric)
                else "harami",
                "neutral",
                "neutral",
                "weak",
                trend,
            )
        )
    if (
        trend == "negative"
        and abs(first.low - second.low) <= tolerance
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "tweezer_bottom",
                "bullish",
                "neutral",
                "weak",
                trend,
            )
        )
    if (
        trend == "positive"
        and abs(first.high - second.high) <= tolerance
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "tweezer_top",
                "bearish",
                "neutral",
                "weak",
                trend,
            )
        )
    if (
        trend == "positive"
        and first.close < first.open
        and second.close > second.open
        and abs(second.open - first.open) <= tolerance
        and _is_long(second, second_metric)
        and second_metric.close_location >= 0.70
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "bullish_separating_lines",
                "bullish",
                "continuation",
                "weak",
                trend,
            )
        )
    if (
        trend == "negative"
        and first.close > first.open
        and second.close < second.open
        and abs(second.open - first.open) <= tolerance
        and _is_long(second, second_metric)
        and second_metric.close_location <= 0.30
    ):
        values.append(
            _pattern(
                bars,
                index - 1,
                index,
                "bearish_separating_lines",
                "bearish",
                "continuation",
                "weak",
                trend,
            )
        )
    return values


def _three_bar_patterns(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> list[_IndexedPattern]:
    if index < 2:
        return []
    first, middle, last = bars[index - 2 : index + 1]
    first_metric, middle_metric, last_metric = metrics[index - 2 : index + 1]
    if (
        first_metric.mb20 is None
        or middle_metric.mb20 is None
        or last_metric.mb20 is None
    ):
        return []
    trend = bars[index - 3].derivative_state if index >= 3 else "zero"
    values: list[_IndexedPattern] = []
    tolerance = 0.05 * cast_float(last_metric.mr20)
    mother_lower = min(first.open, first.close)
    mother_upper = max(first.open, first.close)
    middle_lower = min(middle.open, middle.close)
    middle_upper = max(middle.open, middle.close)
    harami_body = (
        _is_long(first, first_metric)
        and _is_small(middle_metric)
        and middle_lower >= mother_lower - tolerance
        and middle_upper <= mother_upper + tolerance
    )
    if (
        trend == "negative"
        and first.close < first.open
        and harami_body
        and last.close > first.high + tolerance
    ):
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "bullish_harami_confirmed",
                "bullish",
                "reversal",
                "weak",
                trend,
            )
        )
    if (
        trend == "positive"
        and first.close > first.open
        and harami_body
        and last.close < first.low - tolerance
    ):
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "bearish_harami_confirmed",
                "bearish",
                "reversal",
                "weak",
                trend,
            )
        )
    middle_small = _is_small(middle_metric)
    morning = (
        trend == "negative"
        and first.close < first.open
        and _is_long(first, first_metric)
        and middle_small
        and last.close > last.open
        and _is_long(last, last_metric)
        and last.close > (first.open + first.close) / 2
    )
    evening = (
        trend == "positive"
        and first.close > first.open
        and _is_long(first, first_metric)
        and middle_small
        and last.close < last.open
        and _is_long(last, last_metric)
        and last.close < (first.open + first.close) / 2
    )
    if morning:
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "doji_morning_star"
                if _is_doji(middle_metric)
                else "morning_star",
                "bullish",
                "reversal",
                "strong",
                trend,
            )
        )
    if evening:
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "doji_evening_star"
                if _is_doji(middle_metric)
                else "evening_star",
                "bearish",
                "reversal",
                "strong",
                trend,
            )
        )

    three = bars[index - 2 : index + 1]
    three_metrics = metrics[index - 2 : index + 1]
    soldiers = (
        trend in {"negative", "zero"}
        and all(item.close > item.open for item in three)
        and three[0].close < three[1].close < three[2].close
        and all(item.close_location >= 0.70 for item in three_metrics)
        and all(
            item.body / max(cast_float(item.mb20), 1e-9) >= 0.80
            for item in three_metrics
        )
        and all(
            min(three[position - 1].open, three[position - 1].close)
            <= three[position].open
            <= max(three[position - 1].open, three[position - 1].close)
            for position in (1, 2)
        )
    )
    crows = (
        trend == "positive"
        and all(item.close < item.open for item in three)
        and three[0].close > three[1].close > three[2].close
        and all(item.close_location <= 0.30 for item in three_metrics)
        and all(
            item.body / max(cast_float(item.mb20), 1e-9) >= 0.80
            for item in three_metrics
        )
        and all(
            min(three[position - 1].open, three[position - 1].close)
            <= three[position].open
            <= max(three[position - 1].open, three[position - 1].close)
            for position in (1, 2)
        )
    )
    if soldiers:
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "three_white_soldiers",
                "bullish",
                "reversal",
                "strong",
                trend,
            )
        )
    if crows:
        values.append(
            _pattern(
                bars,
                index - 2,
                index,
                "three_black_crows",
                "bearish",
                "reversal",
                "strong",
                trend,
            )
        )
    return values


def _three_methods(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> _IndexedPattern | None:
    for middle_count in range(2, 6):
        start = index - middle_count - 1
        if start < 1:
            continue
        first = bars[start]
        last = bars[index]
        first_metric = metrics[start]
        last_metric = metrics[index]
        if first_metric.mr20 is None or last_metric.mr20 is None:
            continue
        middle_bars = bars[start + 1 : index]
        middle_metrics = metrics[start + 1 : index]
        tolerance = 0.15 * first_metric.mr20
        middle_controlled = all(
            metric.mb20 is not None
            and metric.mb20 > 0
            and metric.body <= 0.80 * metric.mb20
            and bar.high <= first.high + tolerance
            and bar.low >= first.low - tolerance
            for bar, metric in zip(
                middle_bars,
                middle_metrics,
                strict=True,
            )
        )
        if not middle_controlled:
            continue
        trend = bars[start - 1].derivative_state
        middle_volume = statistics.median(
            item.volume for item in middle_bars
        )
        volume_contracts = middle_volume < min(first.volume, last.volume)
        if (
            trend == "positive"
            and first.close > first.open
            and _is_long(first, first_metric)
            and last.close > last.open
            and _is_long(last, last_metric)
            and last.close > first.close
            and volume_contracts
        ):
            return _pattern(
                bars,
                start,
                index,
                "rising_three_methods",
                "bullish",
                "continuation",
                "strong",
                trend,
            )
        if (
            trend == "negative"
            and first.close < first.open
            and _is_long(first, first_metric)
            and last.close < last.open
            and _is_long(last, last_metric)
            and last.close < first.close
            and volume_contracts
        ):
            return _pattern(
                bars,
                start,
                index,
                "falling_three_methods",
                "bearish",
                "continuation",
                "strong",
                trend,
            )
    return None


def _gapping_play(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> _IndexedPattern | None:
    if index < 4 or bars[index].corporate_action:
        return None
    for consolidation_count in range(2, 12):
        start = index - consolidation_count - 1
        if start < 1:
            continue
        first = bars[start]
        final = bars[index]
        first_metric = metrics[start]
        final_metric = metrics[index]
        if (
            first_metric.mr20 is None
            or final_metric.mr20 is None
        ):
            continue
        consolidation = bars[start + 1 : index]
        consolidation_metrics = metrics[start + 1 : index]
        if not all(
            metric.mb20 is not None
            and metric.mb20 > 0
            and metric.body <= 0.80 * metric.mb20
            for metric in consolidation_metrics
        ):
            continue
        trend = bars[start - 1].derivative_state
        tolerance = 0.10 * first_metric.mr20
        body_midpoint = (first.open + first.close) / 2
        if (
            trend == "positive"
            and first.close > first.open
            and _is_long(first, first_metric)
            and min(item.low for item in consolidation)
            >= body_midpoint - tolerance
            and final.low > consolidation[-1].high
            and final.close
            > max(item.high for item in consolidation)
        ):
            return _pattern(
                bars,
                start,
                index,
                "high_price_gapping_play",
                "bullish",
                "continuation",
                "strong",
                trend,
            )
        if (
            trend == "negative"
            and first.close < first.open
            and _is_long(first, first_metric)
            and max(item.high for item in consolidation)
            <= body_midpoint + tolerance
            and final.high < consolidation[-1].low
            and final.close
            < min(item.low for item in consolidation)
        ):
            return _pattern(
                bars,
                start,
                index,
                "low_price_gapping_play",
                "bearish",
                "continuation",
                "strong",
                trend,
            )
    return None


def _window_pattern(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    index: int,
) -> _IndexedPattern | None:
    if index < 1 or bars[index].corporate_action:
        return None
    previous = bars[index - 1]
    current = bars[index]
    trend = previous.derivative_state
    if current.low > previous.high:
        return _pattern(
            bars,
            index - 1,
            index,
            "up_window",
            "bullish",
            "continuation",
            "weak",
            trend,
        )
    if current.high < previous.low:
        return _pattern(
            bars,
            index - 1,
            index,
            "down_window",
            "bearish",
            "continuation",
            "weak",
            trend,
        )
    return None


def _pattern(
    bars: list[DailyTechnicalBar],
    start: int,
    end: int,
    name: PatternName,
    direction: PatternDirection,
    function: PatternFunction,
    strength: PatternStrength,
    trend: DerivativeState,
) -> _IndexedPattern:
    selected = bars[start : end + 1]
    return _IndexedPattern(
        start_index=start,
        end_index=end,
        value=CandlestickSignal(
            pattern=name,
            direction=direction,
            function=function,
            strength=strength,
            started_on=bars[start].date,
            completed_on=bars[end].date,
            low=min(item.low for item in selected),
            high=max(item.high for item in selected),
            context_derivative_state=trend,
        ),
    )


def _deduplicate_patterns(
    patterns: list[_IndexedPattern],
) -> list[_IndexedPattern]:
    priority = {
        "bullish_engulfing": 0,
        "bearish_engulfing": 0,
        "piercing": 1,
        "dark_cloud_cover": 1,
    }
    grouped: dict[
        tuple[int, int, PatternDirection, PatternFunction],
        list[_IndexedPattern],
    ] = {}
    for pattern in patterns:
        key = (
            pattern.start_index,
            pattern.end_index,
            pattern.value.direction,
            pattern.value.function,
        )
        grouped.setdefault(key, []).append(pattern)
    result: list[_IndexedPattern] = []
    for values in grouped.values():
        values.sort(
            key=lambda item: (
                0 if item.value.strength == "strong" else 1,
                priority.get(item.value.pattern, 2),
                item.value.pattern,
            )
        )
        result.append(values[0])
    result.sort(key=lambda item: (item.end_index, item.start_index))
    return result


def _rsi_divergences(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    rsi: list[float | None],
    swings: list[_IndexedSwing],
    patterns: list[_IndexedPattern],
) -> list[RsiDivergenceSignal]:
    result: list[RsiDivergenceSignal] = []
    for index in range(20, len(bars)):
        current_rsi = rsi[index]
        mr20 = metrics[index].mr20
        if current_rsi is None or mr20 is None:
            continue
        trend = bars[index - 1].derivative_state
        direction: DivergenceDirection | None = (
            "bullish"
            if trend == "negative"
            else "bearish"
            if trend == "positive"
            else None
        )
        if direction is None:
            continue
        swing_direction = "low" if direction == "bullish" else "high"
        eligible = [
            item
            for item in swings
            if item.index < index
            and item.value.direction == swing_direction
            and item.value.rsi14 is not None
            and item.value.knowable_on <= bars[index].date
        ]
        if not eligible:
            continue
        historical = eligible[-1]
        historical_rsi = cast_float(historical.value.rsi14)
        tolerance = 0.05 * mr20
        new_extreme = (
            bars[index].close < historical.value.price - tolerance
            if direction == "bullish"
            else bars[index].close > historical.value.price + tolerance
        )
        if not new_extreme:
            continue
        difference = (
            current_rsi - historical_rsi
            if direction == "bullish"
            else historical_rsi - current_rsi
        )
        if difference < 1:
            continue
        strength: DivergenceStrength = (
            "standard" if difference >= 3 else "weak"
        )
        confirmation: DivergenceConfirmation = "none"
        confirmed_index: int | None = None
        same_day_strong = any(
            pattern.end_index == index
            and pattern.value.direction == direction
            and pattern.value.strength == "strong"
            for pattern in patterns
        )
        if same_day_strong:
            confirmation = "candlestick"
            confirmed_index = index
        elif strength == "standard" and index + 1 < len(bars):
            middle = [
                value
                for value in rsi[historical.index + 1 : index]
                if value is not None
            ]
            if middle:
                threshold = (
                    max(middle) if direction == "bullish" else min(middle)
                )
                for confirm_index in range(index + 1, len(bars)):
                    value = rsi[confirm_index]
                    previous = rsi[confirm_index - 1]
                    if value is None or previous is None:
                        continue
                    crossed = (
                        previous <= threshold < value
                        if direction == "bullish"
                        else previous >= threshold > value
                    )
                    if crossed:
                        confirmation = "rsi"
                        confirmed_index = confirm_index
                        break
        result.append(
            RsiDivergenceSignal(
                direction=direction,
                strength=strength,
                status=(
                    "confirmed"
                    if confirmed_index is not None
                    else "candidate"
                ),
                historical_pivot_on=historical.value.occurred_on,
                formed_on=bars[index].date,
                confirmed_on=(
                    bars[confirmed_index].date
                    if confirmed_index is not None
                    else None
                ),
                historical_price=historical.value.price,
                current_price=bars[index].close,
                historical_rsi=historical_rsi,
                current_rsi=current_rsi,
                rsi_difference=difference,
                confirmation=confirmation,
            )
        )
    return result


def _confirmed_composites(
    bars: list[DailyTechnicalBar],
    *,
    patterns: list[_IndexedPattern],
    divergences: list[RsiDivergenceSignal],
    current_index: int,
) -> list[_CompositeEvidence]:
    weak_patterns = [
        item
        for item in patterns
        if item.value.strength == "weak"
        and item.value.direction in {"bullish", "bearish"}
        and item.value.pattern not in {"up_window", "down_window"}
        and current_index - item.end_index <= 10
    ]
    candidates: list[_CompositeEvidence] = []
    for first_index, first in enumerate(weak_patterns):
        for second in weak_patterns[first_index + 1 :]:
            if (
                first.value.direction != second.value.direction
                or first.value.function != second.value.function
                or first.end_index >= second.start_index
                or second.end_index - first.end_index > 5
            ):
                continue
            first_id = _weak_pattern_id(first)
            second_id = _weak_pattern_id(second)
            direction = cast(
                DivergenceDirection,
                second.value.direction,
            )
            candidates.append(
                _CompositeEvidence(
                    direction=direction,
                    confirmed_index=second.end_index,
                    evidence_id=(
                        f"composite:weak-candles:{direction}:"
                        f"{bars[second.end_index].date.isoformat()}"
                    ),
                    components=frozenset({first_id, second_id}),
                )
            )

    for divergence in divergences:
        if divergence.strength != "weak":
            continue
        divergence_index = _date_index(bars, divergence.formed_on)
        if (
            divergence_index is None
            or current_index - divergence_index > 10
        ):
            continue
        divergence_id = (
            f"weak-rsi:{divergence.direction}:"
            f"{divergence.formed_on.isoformat()}"
        )
        for pattern in weak_patterns:
            if (
                pattern.value.direction != divergence.direction
                or pattern.value.function != "reversal"
                or abs(pattern.end_index - divergence_index) > 5
            ):
                continue
            confirmed_index = max(pattern.end_index, divergence_index)
            candidates.append(
                _CompositeEvidence(
                    direction=divergence.direction,
                    confirmed_index=confirmed_index,
                    evidence_id=(
                        f"composite:weak-candle-rsi:"
                        f"{divergence.direction}:"
                        f"{bars[confirmed_index].date.isoformat()}"
                    ),
                    components=frozenset(
                        {
                            _weak_pattern_id(pattern),
                            divergence_id,
                        }
                    ),
                )
            )

    selected: list[_CompositeEvidence] = []
    used_components: set[str] = set()
    unique: set[tuple[str, int, frozenset[str]]] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.confirmed_index,
            item.evidence_id,
        ),
        reverse=True,
    ):
        key = (
            candidate.direction,
            candidate.confirmed_index,
            candidate.components,
        )
        if (
            key in unique
            or candidate.components & used_components
        ):
            continue
        unique.add(key)
        used_components.update(candidate.components)
        selected.append(candidate)
    return selected


def _weak_pattern_id(pattern: _IndexedPattern) -> str:
    return (
        f"weak-candle:{pattern.value.pattern}:"
        f"{pattern.value.completed_on.isoformat()}"
    )


def _technical_levels(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    swings: list[_IndexedSwing],
) -> tuple[list[TechnicalLevel], list[TechnicalLevel]]:
    current_index = len(bars) - 1
    start_index = max(0, current_index - 249)
    current_close = bars[-1].close
    role_reference_close = (
        bars[-2].close if len(bars) >= 2 else current_close
    )
    current_mr = cast_float(metrics[-1].mr20)
    candidates: list[_LevelCandidate] = []
    for swing in swings:
        if swing.index < start_index:
            continue
        role: LevelRole = (
            "support"
            if swing.value.direction == "low"
            else "resistance"
        )
        candidates.append(
            _LevelCandidate(
                role=role,
                lower=swing.value.price,
                center=swing.value.price,
                upper=swing.value.price,
                formed_index=swing.index,
                formed_on=swing.value.occurred_on,
                sources=[
                    "swing_low"
                    if swing.value.direction == "low"
                    else "swing_high"
                ],
            )
        )
    for index in range(max(1, start_index), len(bars)):
        previous = bars[index - 1]
        current = bars[index]
        if current.corporate_action:
            continue
        if current.low > previous.high:
            lower, upper = previous.high, current.low
            source: LevelSource = "up_window"
        elif current.high < previous.low:
            lower, upper = current.high, previous.low
            source = "down_window"
        else:
            continue
        center = (lower + upper) / 2
        candidates.append(
            _LevelCandidate(
                role="support" if source == "up_window" else "resistance",
                lower=lower,
                center=center,
                upper=upper,
                formed_index=index,
                formed_on=current.date,
                sources=[source],
            )
        )
    for end in range(max(start_index + 14, 20), len(bars)):
        window = bars[end - 14 : end + 1]
        mr20 = metrics[end].mr20
        if mr20 is None or mr20 <= 0:
            continue
        closes = [item.close for item in window]
        slope = _linear_slope(closes) / mr20
        p20 = _percentile(closes, 0.20)
        p80 = _percentile(closes, 0.80)
        if abs(slope) > 0.05 or p80 - p20 > 1.5 * mr20:
            continue
        volume_sum = sum(item.volume for item in window)
        typical = [
            (item.high + item.low + item.close) / 3
            for item in window
        ]
        center = (
            sum(
                price * item.volume
                for price, item in zip(typical, window, strict=True)
            )
            / volume_sum
            if volume_sum > 0
            else statistics.mean(typical)
        )
        candidates.append(
            _LevelCandidate(
                role=(
                    "support"
                    if center <= role_reference_close
                    else "resistance"
                ),
                lower=p20,
                center=center,
                upper=p80,
                formed_index=end,
                formed_on=bars[end].date,
                sources=["consolidation"],
            )
        )
    candidates.extend(
        _trendline_candidates(
            bars,
            metrics,
            swings,
            start_index=start_index,
        )
    )
    structural_candidates = list(candidates)
    candidates.extend(
        _polarity_conversion_candidates(
            structural_candidates,
            bars,
            metrics,
        )
    )
    candidates.extend(
        _failed_breakout_candidates(
            structural_candidates,
            bars,
            metrics,
        )
    )
    active = [
        candidate
        for candidate in candidates
        if not _level_invalidated(candidate, bars, metrics)
    ]
    merged = _merge_levels(active, current_mr)
    supports = sorted(
        (
            level
            for level in merged
            if level.role == "support"
            and level.lower <= current_close
        ),
        key=lambda level: _support_sort_key(
            level,
            current_close=current_close,
        ),
    )
    resistances = sorted(
        (
            level
            for level in merged
            if level.role == "resistance"
        ),
        key=lambda level: _resistance_sort_key(
            level,
            current_close=current_close,
        ),
    )
    return supports, resistances


def _trendline_candidates(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    swings: list[_IndexedSwing],
    *,
    start_index: int,
) -> list[_LevelCandidate]:
    current_index = len(bars) - 1
    current_mr = cast_float(metrics[-1].mr20)
    result: list[_LevelCandidate] = []
    for direction, role, source in (
        ("low", "support", "ascending_trendline"),
        ("high", "resistance", "descending_trendline"),
    ):
        points = [
            item
            for item in swings
            if item.value.direction == direction
            and item.index >= start_index
        ]
        for first, second in zip(points, points[1:], strict=False):
            distance = second.index - first.index
            second_mr = metrics[second.index].mr20
            if distance <= 0 or second_mr is None:
                continue
            tolerance = 0.05 * second_mr
            valid_direction = (
                second.value.price > first.value.price + tolerance
                if direction == "low"
                else second.value.price < first.value.price - tolerance
            )
            if not valid_direction:
                continue
            formed_index = second.index + 3
            if formed_index > current_index:
                continue
            slope = (
                second.value.price - first.value.price
            ) / distance
            projected = (
                second.value.price
                + slope * (current_index - second.index)
            )
            half_width = 0.10 * current_mr
            result.append(
                _LevelCandidate(
                    role=cast(LevelRole, role),
                    lower=projected - half_width,
                    center=projected,
                    upper=projected + half_width,
                    formed_index=formed_index,
                    formed_on=second.value.knowable_on,
                    sources=[cast(LevelSource, source)],
                    slope_per_trading_day=slope,
                )
            )
    return result


def _polarity_conversion_candidates(
    candidates: list[_LevelCandidate],
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
) -> list[_LevelCandidate]:
    result: list[_LevelCandidate] = []
    for level in candidates:
        for index in range(level.formed_index + 1, len(bars) - 1):
            metric = metrics[index]
            lower, upper = _level_bounds_at(
                level,
                index=index,
                current_index=len(bars) - 1,
            )
            upward_break = (
                level.role == "resistance"
                and (
                    _price_breaks_resistance(
                        lower=lower,
                        upper=upper,
                        current_close=bars[index].close,
                    )
                    or (
                        _volume_confirmed(metric)
                        and bars[index].close
                        > upper + 0.10 * cast_float(metric.mr20)
                    )
                )
            )
            downward_break = (
                level.role == "support"
                and _volume_confirmed(metric)
                and bars[index].close
                < lower - 0.10 * cast_float(metric.mr20)
            )
            if not upward_break and not downward_break:
                continue
            result.append(
                _LevelCandidate(
                    role=(
                        "support"
                        if upward_break
                        else "resistance"
                    ),
                    lower=level.lower,
                    center=level.center,
                    upper=level.upper,
                    formed_index=index,
                    formed_on=bars[index].date,
                    sources=list(
                        dict.fromkeys(
                            [*level.sources, "polarity_conversion"]
                        )
                    ),
                    slope_per_trading_day=(
                        level.slope_per_trading_day
                    ),
                )
            )
            break
    return result


def _failed_breakout_candidates(
    candidates: list[_LevelCandidate],
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
) -> list[_LevelCandidate]:
    result: list[_LevelCandidate] = []
    current_index = len(bars) - 1
    for level in candidates:
        found = False
        for test_index in range(
            level.formed_index + 1,
            len(bars) - 1,
        ):
            metric = metrics[test_index]
            if metric.mr20 is None:
                continue
            lower, upper = _level_bounds_at(
                level,
                index=test_index,
                current_index=current_index,
            )
            upward_test = (
                level.role == "resistance"
                and bars[test_index].high
                > upper + 0.10 * metric.mr20
            )
            downward_test = (
                level.role == "support"
                and bars[test_index].low
                < lower - 0.10 * metric.mr20
            )
            if not upward_test and not downward_test:
                continue
            return_end = min(test_index + 3, len(bars) - 1)
            for return_index in range(
                test_index + 1,
                return_end + 1,
            ):
                return_mr = metrics[return_index].mr20
                if return_mr is None:
                    continue
                return_lower, return_upper = _level_bounds_at(
                    level,
                    index=return_index,
                    current_index=current_index,
                )
                upward_failed = (
                    upward_test
                    and bars[return_index].close
                    < return_lower - 0.10 * return_mr
                )
                downward_failed = (
                    downward_test
                    and bars[return_index].close
                    > return_upper + 0.10 * return_mr
                )
                if not upward_failed and not downward_failed:
                    continue
                tested = bars[test_index : return_index + 1]
                price = (
                    max(item.high for item in tested)
                    if upward_failed
                    else min(item.low for item in tested)
                )
                result.append(
                    _LevelCandidate(
                        role=(
                            "resistance"
                            if upward_failed
                            else "support"
                        ),
                        lower=price,
                        center=price,
                        upper=price,
                        formed_index=return_index,
                        formed_on=bars[return_index].date,
                        sources=[
                            "failed_breakout_high"
                            if upward_failed
                            else "failed_breakout_low"
                        ],
                    )
                )
                found = True
                break
            if found:
                break
    return result


def _level_bounds_at(
    level: _LevelCandidate,
    *,
    index: int,
    current_index: int,
) -> tuple[float, float]:
    offset = index - current_index
    shift = level.slope_per_trading_day * offset
    return level.lower + shift, level.upper + shift


def _volume_confirmed(metric: _Metrics) -> bool:
    return (
        metric.mr20 is not None
        and metric.volume_ratio is not None
        and metric.turnover_ratio is not None
        and metric.volume_ratio >= 1.5
        and metric.turnover_ratio >= 1.5
    )


def _level_invalidated(
    level: _LevelCandidate,
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
) -> bool:
    # 当日突破先保留到分析末尾，供突破判定和压力转支撑使用。
    for index in range(level.formed_index + 1, len(bars) - 1):
        metric = metrics[index]
        lower, upper = _level_bounds_at(
            level,
            index=index,
            current_index=len(bars) - 1,
        )
        if (
            level.role == "resistance"
            and _price_breaks_resistance(
                lower=lower,
                upper=upper,
                current_close=bars[index].close,
            )
        ):
            return True
        if not _volume_confirmed(metric):
            continue
        mr20 = cast(float, metric.mr20)
        if (
            level.role == "support"
            and bars[index].close
            < lower - 0.10 * mr20
        ):
            return True
        if (
            level.role == "resistance"
            and bars[index].close
            > upper + 0.10 * mr20
        ):
            return True
    return False


def _merge_levels(
    candidates: list[_LevelCandidate],
    mr20: float,
) -> list[TechnicalLevel]:
    ordered = sorted(
        candidates,
        key=lambda item: (item.formed_index, item.center),
        reverse=True,
    )
    merged: list[_LevelCandidate] = []
    for candidate in ordered:
        existing = next(
            (
                level
                for level in merged
                if level.role == candidate.role
                and abs(level.center - candidate.center) <= 0.25 * mr20
            ),
            None,
        )
        if existing is None:
            merged.append(candidate)
            continue
        existing.lower = min(existing.lower, candidate.lower)
        existing.upper = max(existing.upper, candidate.upper)
        existing.sources.extend(candidate.sources)
    return [
        TechnicalLevel(
            role=item.role,
            lower=item.lower,
            center=item.center,
            upper=item.upper,
            formed_on=item.formed_on,
            sources=list(dict.fromkeys(item.sources)),
            source_count=len(set(item.sources)),
            slope_per_trading_day=item.slope_per_trading_day,
        )
        for item in merged
    ]


def _support_retest(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    supports: list[TechnicalLevel],
) -> bool:
    if not supports or metrics[-1].mr20 is None:
        return False
    support = supports[0]
    tolerance = 0.25 * metrics[-1].mr20
    current = bars[-1]
    return (
        current.low <= support.upper + tolerance
        and current.close >= support.lower
        and not (
            current.close < support.lower - 0.10 * metrics[-1].mr20
        )
    )


def _support_sort_key(
    level: TechnicalLevel,
    *,
    current_close: float,
) -> tuple[float, int, float]:
    return (
        max(0.0, current_close - level.upper),
        -level.formed_on.toordinal(),
        -level.center,
    )


def _resistance_sort_key(
    level: TechnicalLevel,
    *,
    current_close: float,
) -> tuple[float, int, float]:
    if current_close < level.lower:
        distance = level.lower - current_close
    elif current_close > level.upper:
        distance = current_close - level.upper
    else:
        distance = 0.0
    return (
        distance,
        -level.formed_on.toordinal(),
        level.center,
    )


def _pressure_target(
    *,
    current_close: float,
    resistances: list[TechnicalLevel],
) -> float | None:
    higher = [
        resistance.lower
        for resistance in resistances
        if resistance.lower > current_close
    ]
    return min(higher) if higher else None


def _price_breaks_resistance(
    *,
    lower: float,
    upper: float,
    current_close: float,
) -> bool:
    width = upper - lower
    return (
        width > 0
        and current_close >= upper + 0.5 * width
    )


def _volume_breaks_resistance(
    resistance: TechnicalLevel,
    *,
    current_close: float,
    metric: _Metrics,
) -> bool:
    return (
        _volume_confirmed(metric)
        and metric.mr20 is not None
        and current_close
        > resistance.upper + 0.10 * metric.mr20
    )


def _broken_resistances(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    resistances: list[TechnicalLevel],
) -> list[TechnicalLevel]:
    metric = metrics[-1]
    if not resistances:
        return []
    broken = [
        resistance
        for resistance in resistances
        if (
            _volume_breaks_resistance(
                resistance,
                current_close=bars[-1].close,
                metric=metric,
            )
            or _price_breaks_resistance(
                lower=resistance.lower,
                upper=resistance.upper,
                current_close=bars[-1].close,
            )
        )
    ]
    return sorted(
        broken,
        key=lambda resistance: _resistance_sort_key(
            resistance,
            current_close=bars[-1].close,
        ),
    )


def _shrinking_pullback_restart(
    bars: list[DailyTechnicalBar],
    metrics: list[_Metrics],
    supports: list[TechnicalLevel],
) -> bool:
    current = bars[-1]
    current_metric = metrics[-1]
    if (
        not supports
        or current_metric.mr20 is None
        or current.close <= current.open
    ):
        return False
    for pullback_length in range(2, 6):
        start = len(bars) - pullback_length - 1
        if start < 20:
            continue
        impulse = bars[max(0, start - 3) : start + 1]
        pullback = bars[start + 1 : -1]
        if bars[start].derivative_state != "positive":
            continue
        impulse_volume = statistics.median(item.volume for item in impulse)
        pullback_volume = statistics.median(
            item.volume for item in pullback
        )
        if impulse_volume <= 0 or pullback_volume / impulse_volume > 0.8:
            continue
        support = supports[0]
        if min(item.close for item in pullback) < support.lower:
            continue
        pullback_high = max(item.high for item in pullback)
        if current.close > pullback_high + 0.05 * current_metric.mr20:
            return True
    return False


def _reward_risk(
    *,
    entry: float,
    stop: float | None,
    target: float | None,
) -> float | None:
    if stop is None or target is None:
        return None
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _is_long(
    bar: DailyTechnicalBar,
    metric: _Metrics,
) -> bool:
    if metric.mb20 is None:
        return False
    return (
        metric.body >= 1.2 * max(metric.mb20, bar.close * 0.0001)
        and metric.body / metric.range >= 0.60
    )


def _is_small(metric: _Metrics) -> bool:
    if metric.mb20 is None:
        return False
    return (
        metric.body <= 0.60 * metric.mb20
        or metric.body / metric.range <= 0.30
    )


def _is_doji(metric: _Metrics) -> bool:
    if metric.mb20 is None:
        return False
    return (
        metric.body / metric.range <= 0.10
        and metric.body <= 0.30 * metric.mb20
    )


def _linear_slope(values: list[float]) -> float:
    size = len(values)
    x_mean = (size - 1) / 2
    y_mean = statistics.mean(values)
    numerator = sum(
        (index - x_mean) * (value - y_mean)
        for index, value in enumerate(values)
    )
    denominator = sum((index - x_mean) ** 2 for index in range(size))
    return numerator / denominator if denominator else 0


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _date_index(
    bars: list[DailyTechnicalBar],
    target: date,
) -> int | None:
    return next(
        (index for index, bar in enumerate(bars) if bar.date == target),
        None,
    )


def cast_float(value: float | None) -> float:
    assert value is not None
    return value


def cast_int(value: int | None) -> int:
    assert value is not None
    return value
