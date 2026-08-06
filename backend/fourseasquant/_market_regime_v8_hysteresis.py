"""第八版正式内核：在第三版自适应强触发之上增加维持与退出滞后。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fourseasquant._market_regime_v8_structure import TrendState
from fourseasquant._market_regime_v8_adaptive import AdaptiveMarketTechnicalDay
from fourseasquant._market_regime_v8_technical import MarketTechnicalRegimeDay


Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class HysteresisMarketTechnicalDay:
    trading_date: date
    state: TrendState
    base_structure_state: TrendState
    adaptive_v3_state: TrendState
    active_direction: Direction | None
    bullish_trigger: bool
    bearish_trigger: bool
    within_minimum_hold: bool
    continuation_supported: bool
    continuation_extension: bool
    unsupported_streak: int
    exited_after_failures: bool
    bullish_score_threshold: float
    bearish_score_threshold: float
    bullish_relative_threshold: float
    bearish_relative_threshold: float
    evidence: MarketTechnicalRegimeDay


def apply_hysteresis(
    adaptive_days: list[AdaptiveMarketTechnicalDay],
    *,
    minimum_hold_days: int = 5,
    continuation_score_ratio: float = 0.70,
    continuation_relative_ratio: float = 0.50,
    exit_failure_days: int = 2,
) -> list[HysteresisMarketTechnicalDay]:
    """强条件进入，至少持有五日，弱条件维持，连续失效后退出。

    强触发完全复用第三版，维持条件只使用当日收盘证据与当日已由历史生成的
    自适应门槛。反向强触发立即翻转，同向强触发刷新最低持有期。
    """
    if minimum_hold_days < 1:
        raise ValueError("最低持有期必须至少为1日")
    if not 0 <= continuation_score_ratio <= 1:
        raise ValueError("维持快速分比例必须位于0到1")
    if not 0 <= continuation_relative_ratio <= 1:
        raise ValueError("维持相对强度比例必须位于0到1")
    if exit_failure_days < 1:
        raise ValueError("连续失效退出天数必须至少为1日")

    active_direction: Direction | None = None
    minimum_hold_until = -1
    unsupported_streak = 0
    result: list[HysteresisMarketTechnicalDay] = []

    for index, item in enumerate(adaptive_days):
        if item.bullish_trigger or item.bearish_trigger:
            if item.bullish_trigger and item.bearish_trigger:
                active_direction = (
                    "bullish"
                    if item.evidence.bullish_fast_score
                    >= item.evidence.bearish_fast_score
                    else "bearish"
                )
            else:
                active_direction = "bullish" if item.bullish_trigger else "bearish"
            minimum_hold_until = index + minimum_hold_days - 1
            unsupported_streak = 0

        within_minimum_hold = (
            active_direction is not None and index <= minimum_hold_until
        )
        continuation_supported = False
        exited_after_failures = False
        if active_direction is not None and not within_minimum_hold:
            if active_direction == "bullish":
                continuation_supported = (
                    item.evidence.bullish_fast_score
                    >= continuation_score_ratio * item.bullish_score_threshold
                    and item.evidence.relative_strength
                    >= continuation_relative_ratio * item.bullish_relative_threshold
                )
            else:
                continuation_supported = (
                    item.evidence.bearish_fast_score
                    >= continuation_score_ratio * item.bearish_score_threshold
                    and -item.evidence.relative_strength
                    >= continuation_relative_ratio * item.bearish_relative_threshold
                )
            if continuation_supported:
                unsupported_streak = 0
            else:
                unsupported_streak += 1
            if unsupported_streak >= exit_failure_days:
                active_direction = None
                unsupported_streak = 0
                exited_after_failures = True

        continuation_extension = (
            active_direction is not None and index > minimum_hold_until
        )
        state: TrendState = (
            "rising"
            if active_direction == "bullish"
            else "falling"
            if active_direction == "bearish"
            else item.base_structure_state
        )
        result.append(
            HysteresisMarketTechnicalDay(
                trading_date=item.trading_date,
                state=state,
                base_structure_state=item.base_structure_state,
                adaptive_v3_state=item.state,
                active_direction=active_direction,
                bullish_trigger=item.bullish_trigger,
                bearish_trigger=item.bearish_trigger,
                within_minimum_hold=within_minimum_hold,
                continuation_supported=continuation_supported,
                continuation_extension=continuation_extension,
                unsupported_streak=unsupported_streak,
                exited_after_failures=exited_after_failures,
                bullish_score_threshold=item.bullish_score_threshold,
                bearish_score_threshold=item.bearish_score_threshold,
                bullish_relative_threshold=item.bullish_relative_threshold,
                bearish_relative_threshold=item.bearish_relative_threshold,
                evidence=item.evidence,
            )
        )
    return result
