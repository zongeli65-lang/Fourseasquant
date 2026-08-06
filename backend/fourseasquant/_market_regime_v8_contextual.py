"""第八版正式内核：给稳定技术状态增加趋势背景动能反转。

原型问题：只有在此前已经持续并大幅上涨时，高位衰竭或极端负向冲击再经
跨指数验证，是否能够及时解除上行锁定并确认下行，同时避免普通阴线误判？

本模块只做纯计算，不读写文件或数据库。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fourseasquant._market_regime_v8_structure import MarketBar, TrendState
from fourseasquant._market_regime_v8_hysteresis import HysteresisMarketTechnicalDay
from fourseasquant._market_regime_v8_momentum import (
    CandlestickMomentumEvidence,
    CandlestickMomentumStateDay,
    MomentumEvent,
    MomentumPhase,
)


Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class ContextualMomentumDay:
    trading_date: date
    state: TrendState
    previous_state: TrendState
    baseline_state: TrendState
    phase: MomentumPhase
    event: MomentumEvent
    prior_directional_streak: int
    prior_move_atr: float
    prior_directional_event_count: int
    overextended_context: Direction | None
    reversal_candidate: Direction | None
    candidate_age: int
    strong_reversal_verified: bool
    contextual_takeover: bool
    active_override: Direction | None
    released_to_sideways: bool
    evidence: CandlestickMomentumEvidence
    reason: str


def _prior_streak(
    baseline_days: list[HysteresisMarketTechnicalDay],
    index: int,
    direction: Direction,
) -> int:
    expected: TrendState = "rising" if direction == "bullish" else "falling"
    streak = 0
    for cursor in range(index - 1, -1, -1):
        if baseline_days[cursor].state != expected:
            break
        streak += 1
    return streak


def _prior_move_atr(
    bars: list[MarketBar],
    baseline_days: list[HysteresisMarketTechnicalDay],
    index: int,
    direction: Direction,
    windows: tuple[int, ...],
) -> float:
    if index < 2:
        return 0.0
    previous_index = index - 1
    atr = baseline_days[previous_index].evidence.sh_structure.atr10
    if atr <= 0:
        return 0.0
    signed_moves: list[float] = []
    for window in windows:
        start = max(0, previous_index - window)
        move = (bars[previous_index].close - bars[start].close) / atr
        signed_moves.append(move if direction == "bullish" else -move)
    return max(signed_moves, default=0.0)


def _prior_directional_events(
    momentum_days: list[CandlestickMomentumStateDay],
    index: int,
    direction: Direction,
    lookback_days: int,
) -> int:
    wanted = (
        {"bullish_shock", "bullish_impulse"}
        if direction == "bullish"
        else {"bearish_shock", "bearish_impulse"}
    )
    return sum(
        item.event in wanted
        for item in momentum_days[max(0, index - lookback_days) : index]
    )


def _verified_reversal(
    item: CandlestickMomentumStateDay,
    direction: Direction,
    *,
    minimum_body_atr: float,
    minimum_breadth: int,
    minimum_one_atr_breadth: int,
) -> bool:
    evidence = item.evidence
    if direction == "bearish":
        return (
            item.event == "bearish_shock"
            and evidence.declining_count >= minimum_breadth
            and evidence.bearish_one_atr_count >= minimum_one_atr_breadth
            and evidence.body_impulse_atr <= -minimum_body_atr
            and evidence.close_location <= 0
        )
    return (
        item.event == "bullish_shock"
        and evidence.advancing_count >= minimum_breadth
        and evidence.bullish_one_atr_count >= minimum_one_atr_breadth
        and evidence.body_impulse_atr >= minimum_body_atr
        and evidence.close_location >= 0
    )


def apply_contextual_momentum_override(
    bars: list[MarketBar],
    baseline_days: list[HysteresisMarketTechnicalDay],
    momentum_days: list[CandlestickMomentumStateDay],
    *,
    minimum_prior_streak: int = 4,
    prior_move_windows: tuple[int, ...] = (5, 10),
    minimum_prior_move_atr: float = 4.0,
    directional_event_lookback: int = 5,
    minimum_directional_events: int = 3,
    confirmation_window_days: int = 2,
    minimum_reversal_body_atr: float = 0.50,
    minimum_reversal_breadth: int = 4,
    minimum_one_atr_breadth: int = 3,
) -> list[ContextualMomentumDay]:
    """把持续大幅趋势作为前提，再用衰竭、强反向冲击和扩散完成反转。

    顶部链路与底部链路完全对称。衰竭日只解除原趋势并转为震荡候选；随后
    出现经跨指数实体、收盘位置和一倍ATR数量验证的反向冲击，才确认接管。
    """
    if not (len(bars) == len(baseline_days) == len(momentum_days)):
        raise ValueError("行情、基础状态和动能状态长度必须一致")
    dates = [item.trading_date for item in bars]
    if dates != [item.trading_date for item in baseline_days]:
        raise ValueError("行情与基础状态交易日不一致")
    if dates != [item.trading_date for item in momentum_days]:
        raise ValueError("行情与动能状态交易日不一致")
    if minimum_prior_streak < 1 or confirmation_window_days < 1:
        raise ValueError("趋势持续日和确认窗口必须至少为1日")

    state: TrendState = baseline_days[0].state if baseline_days else "sideways"
    candidate: Direction | None = None
    candidate_age = 0
    active_override: Direction | None = None
    result: list[ContextualMomentumDay] = []

    for index, (baseline, momentum) in enumerate(zip(baseline_days, momentum_days)):
        previous_state = state
        released_to_sideways = False
        contextual_takeover = False
        strong_reversal_verified = False

        bullish_streak = _prior_streak(baseline_days, index, "bullish")
        bearish_streak = _prior_streak(baseline_days, index, "bearish")
        bullish_move_atr = _prior_move_atr(
            bars, baseline_days, index, "bullish", prior_move_windows
        )
        bearish_move_atr = _prior_move_atr(
            bars, baseline_days, index, "bearish", prior_move_windows
        )
        bullish_events = _prior_directional_events(
            momentum_days, index, "bullish", directional_event_lookback
        )
        bearish_events = _prior_directional_events(
            momentum_days, index, "bearish", directional_event_lookback
        )
        bullish_context = (
            bullish_streak >= minimum_prior_streak
            and bullish_move_atr >= minimum_prior_move_atr
            and bullish_events >= minimum_directional_events
        )
        bearish_context = (
            bearish_streak >= minimum_prior_streak
            and bearish_move_atr >= minimum_prior_move_atr
            and bearish_events >= minimum_directional_events
        )
        overextended_context: Direction | None = (
            "bullish" if bullish_context else "bearish" if bearish_context else None
        )

        verified_bearish = _verified_reversal(
            momentum,
            "bearish",
            minimum_body_atr=minimum_reversal_body_atr,
            minimum_breadth=minimum_reversal_breadth,
            minimum_one_atr_breadth=minimum_one_atr_breadth,
        )
        verified_bullish = _verified_reversal(
            momentum,
            "bullish",
            minimum_body_atr=minimum_reversal_body_atr,
            minimum_breadth=minimum_reversal_breadth,
            minimum_one_atr_breadth=minimum_one_atr_breadth,
        )

        reason = "沿用第七版稳定技术状态，未形成完整的上下文反转链路"

        if active_override == "bearish":
            if momentum.phase in ("bearish_impulse", "bearish_exhaustion"):
                state = "falling"
                reason = "经上涨背景确认的空头接管仍在延续"
            else:
                active_override = None
                state = "sideways"
                released_to_sideways = True
                reason = "空头动能不再延续，先解除下行并转为震荡"
        elif active_override == "bullish":
            if momentum.phase in ("bullish_impulse", "bullish_exhaustion"):
                state = "rising"
                reason = "经下跌背景确认的多头接管仍在延续"
            else:
                active_override = None
                state = "sideways"
                released_to_sideways = True
                reason = "多头动能不再延续，先解除上行并转为震荡"
        else:
            candidate_age = candidate_age + 1 if candidate is not None else 0
            if candidate == "bearish" and verified_bearish:
                active_override = "bearish"
                candidate = None
                candidate_age = 0
                state = "falling"
                strong_reversal_verified = True
                contextual_takeover = True
                reason = "持续大幅上涨后的顶部衰竭获得跨指数强下降验证，确认进入下行"
            elif candidate == "bullish" and verified_bullish:
                active_override = "bullish"
                candidate = None
                candidate_age = 0
                state = "rising"
                strong_reversal_verified = True
                contextual_takeover = True
                reason = "持续大幅下跌后的底部衰竭获得跨指数强上升验证，确认进入上行"
            elif bullish_context and verified_bearish:
                active_override = "bearish"
                candidate = None
                candidate_age = 0
                state = "falling"
                strong_reversal_verified = True
                contextual_takeover = True
                reason = "持续大幅上涨后出现内部已验证的极端负向冲击，确认进入下行"
            elif bearish_context and verified_bullish:
                active_override = "bullish"
                candidate = None
                candidate_age = 0
                state = "rising"
                strong_reversal_verified = True
                contextual_takeover = True
                reason = "持续大幅下跌后出现内部已验证的极端正向冲击，确认进入上行"
            elif bullish_context and momentum.event == "top_exhaustion":
                candidate = "bearish"
                candidate_age = 0
                state = "sideways"
                reason = "持续大幅上涨后出现高位衰竭，解除上行并等待强下降验证"
            elif bearish_context and momentum.event == "bottom_exhaustion":
                candidate = "bullish"
                candidate_age = 0
                state = "sideways"
                reason = "持续大幅下跌后出现低位衰竭，解除下行并等待强上升验证"
            elif candidate is not None and candidate_age <= confirmation_window_days:
                opposite_recovery = (
                    candidate == "bearish"
                    and momentum.event in ("bullish_shock", "bullish_impulse")
                ) or (
                    candidate == "bullish"
                    and momentum.event in ("bearish_shock", "bearish_impulse")
                )
                if opposite_recovery:
                    candidate = None
                    candidate_age = 0
                    state = baseline.state
                    reason = "原趋势动能重新建立，取消反转候选"
                else:
                    state = "sideways"
                    reason = "衰竭候选仍在确认窗口内，暂按震荡等待反向验证"
            else:
                candidate = None
                candidate_age = 0
                state = baseline.state

        directional_streak = bullish_streak if bullish_context else bearish_streak
        directional_move = bullish_move_atr if bullish_context else bearish_move_atr
        directional_events = bullish_events if bullish_context else bearish_events
        result.append(
            ContextualMomentumDay(
                trading_date=momentum.trading_date,
                state=state,
                previous_state=previous_state,
                baseline_state=baseline.state,
                phase=momentum.phase,
                event=momentum.event,
                prior_directional_streak=directional_streak,
                prior_move_atr=directional_move,
                prior_directional_event_count=directional_events,
                overextended_context=overextended_context,
                reversal_candidate=candidate,
                candidate_age=candidate_age,
                strong_reversal_verified=strong_reversal_verified,
                contextual_takeover=contextual_takeover,
                active_override=active_override,
                released_to_sideways=released_to_sideways,
                evidence=momentum.evidence,
                reason=reason,
            )
        )
    return result
