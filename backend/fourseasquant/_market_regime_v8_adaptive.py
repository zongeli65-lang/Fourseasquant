"""第八版正式内核：对市场快速技术证据应用只看历史的自适应阈值。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fourseasquant._market_regime_v8_structure import TrendState
from fourseasquant._market_regime_v8_technical import MarketTechnicalRegimeDay


Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class AdaptiveMarketTechnicalDay:
    trading_date: date
    state: TrendState
    base_structure_state: TrendState
    fixed_v2_state: TrendState
    fast_override: Direction | None
    bullish_trigger: bool
    bearish_trigger: bool
    bullish_score_threshold: float
    bearish_score_threshold: float
    bullish_relative_threshold: float
    bearish_relative_threshold: float
    evidence: MarketTechnicalRegimeDay


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def apply_adaptive_thresholds(
    evidence_days: list[MarketTechnicalRegimeDay],
    *,
    lookback_days: int = 120,
    minimum_history_days: int = 40,
    quantile: float = 0.85,
    minimum_score_threshold: float = 6.5,
    minimum_relative_threshold: float = 0.30,
    fast_memory_days: int = 5,
) -> list[AdaptiveMarketTechnicalDay]:
    """使用前一日以前的同方向历史分布生成当日门槛。"""
    active_direction: Direction | None = None
    active_until = -1
    result: list[AdaptiveMarketTechnicalDay] = []
    for index, item in enumerate(evidence_days):
        history = evidence_days[max(0, index - lookback_days) : index]
        if len(history) >= minimum_history_days:
            bullish_scores = [
                value.bullish_fast_score
                for value in history
                if value.bullish_fast_score > 0
            ]
            bearish_scores = [
                value.bearish_fast_score
                for value in history
                if value.bearish_fast_score > 0
            ]
            bullish_relative = [
                value.relative_strength
                for value in history
                if value.relative_strength > 0
            ]
            bearish_relative = [
                -value.relative_strength
                for value in history
                if value.relative_strength < 0
            ]
            bullish_score_threshold = max(
                minimum_score_threshold,
                _quantile(bullish_scores, quantile),
            )
            bearish_score_threshold = max(
                minimum_score_threshold,
                _quantile(bearish_scores, quantile),
            )
            bullish_relative_threshold = max(
                minimum_relative_threshold,
                _quantile(bullish_relative, quantile),
            )
            bearish_relative_threshold = max(
                minimum_relative_threshold,
                _quantile(bearish_relative, quantile),
            )
        else:
            bullish_score_threshold = minimum_score_threshold
            bearish_score_threshold = minimum_score_threshold
            bullish_relative_threshold = minimum_relative_threshold
            bearish_relative_threshold = minimum_relative_threshold

        bullish_trigger = (
            item.bullish_breakout_count >= 1
            and item.bullish_fast_score >= bullish_score_threshold
            and item.relative_strength >= bullish_relative_threshold
        )
        bearish_trigger = (
            item.bearish_breakout_count >= 1
            and item.bearish_fast_score >= bearish_score_threshold
            and -item.relative_strength >= bearish_relative_threshold
        )
        if bullish_trigger or bearish_trigger:
            if bullish_trigger and bearish_trigger:
                active_direction = (
                    "bullish"
                    if item.bullish_fast_score >= item.bearish_fast_score
                    else "bearish"
                )
            else:
                active_direction = "bullish" if bullish_trigger else "bearish"
            active_until = index + fast_memory_days - 1
        elif index > active_until:
            active_direction = None

        state: TrendState = (
            "rising"
            if active_direction == "bullish"
            else "falling"
            if active_direction == "bearish"
            else item.base_structure_state
        )
        result.append(
            AdaptiveMarketTechnicalDay(
                trading_date=item.trading_date,
                state=state,
                base_structure_state=item.base_structure_state,
                fixed_v2_state=item.state,
                fast_override=active_direction,
                bullish_trigger=bullish_trigger,
                bearish_trigger=bearish_trigger,
                bullish_score_threshold=bullish_score_threshold,
                bearish_score_threshold=bearish_score_threshold,
                bullish_relative_threshold=bullish_relative_threshold,
                bearish_relative_threshold=bearish_relative_threshold,
                evidence=item,
            )
        )
    return result
