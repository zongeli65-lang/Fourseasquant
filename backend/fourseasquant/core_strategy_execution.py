from __future__ import annotations

import math
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from fourseasquant.core_strategy import (
    CORE_STRATEGY_VERSION,
    CandidateRoute,
    FundamentalPriority,
    OpportunityGrade,
)


MarketState = Literal["rising", "sideways", "falling"]
BaseOpportunityGrade = Literal["B", "C"]
EntryTrigger = Literal[
    "support_retest",
    "strong_bullish_candlestick",
    "standard_rsi_bottom_divergence",
    "confirmed_bullish_composite",
    "shrinking_volume_pullback_restart",
]
PricePriority = Literal["limit_up", "big_bullish_candle", "normal"]

_GRADE_POINTS: dict[BaseOpportunityGrade, int] = {"C": 0, "B": 1}
_POINTS_GRADE: dict[int, OpportunityGrade] = {
    0: "C",
    1: "B",
    2: "A",
    3: "S",
}
_POSITION_FRACTION: dict[OpportunityGrade, float] = {
    "C": 0.25,
    "B": 0.5,
    "A": 0.75,
    "S": 1,
}
_MARKET_POSITION_FACTOR: dict[MarketState, float] = {
    "rising": 1.0,
    "sideways": 0.5,
    "falling": 0.2,
}


class FeeSchedule(BaseModel):
    commission_rate: float = Field(default=0.00025, ge=0)
    minimum_commission: float = Field(default=5, ge=0)
    transfer_fee_rate: float = Field(default=0.00001, ge=0)
    stamp_tax_rate: float = Field(default=0.0005, ge=0)


class EntryCandidate(BaseModel):
    """已经通过初筛和证据回填、等待当日交易判断的候选。"""

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    route: CandidateRoute
    base_grade: BaseOpportunityGrade
    preliminary_rank: int = Field(ge=1)
    entry_triggers: list[EntryTrigger] = Field(default_factory=list)
    strong_evidence_ids: list[str] = Field(default_factory=list)
    net_reward_risk_ratio: float | None = Field(default=None, ge=0)
    favorable_opinion: bool = False
    negative_opinion: bool = False
    standard_rsi_top_divergence: bool = False
    strong_bearish_veto: bool = False
    weak_bearish_rsi_composite_veto: bool = False
    opinion_targeted: bool = False
    fundamental_priority: FundamentalPriority = "neutral"
    price_priority: PricePriority = "normal"
    support_source_count: int = Field(default=0, ge=0)
    close: float = Field(gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    pressure_target: float | None = Field(default=None, gt=0)
    suspended: bool = False
    at_limit_down: bool = False

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if len(set(self.entry_triggers)) != len(self.entry_triggers):
            raise ValueError("当日分歧买入证据不能重复")
        if len(set(self.strong_evidence_ids)) != len(self.strong_evidence_ids):
            raise ValueError("强技术证据必须相互独立且标识不能重复")
        if any(not evidence.strip() for evidence in self.strong_evidence_ids):
            raise ValueError("强技术证据标识不能为空")
        return self


class PortfolioForEntry(BaseModel):
    initial_capital: float = Field(gt=0)
    net_asset_value: float = Field(gt=0)
    available_cash: float = Field(ge=0)
    held_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_held_codes(self) -> Self:
        if len(set(self.held_codes)) != len(self.held_codes):
            raise ValueError("当前持仓股票代码不能重复")
        if any(len(code) != 6 or not code.isdigit() for code in self.held_codes):
            raise ValueError("当前持仓股票代码必须为六位数字")
        return self


class EntryPlanningInput(BaseModel):
    """新开仓计划模块的唯一输入。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    market_state: MarketState
    market_data_complete: bool
    portfolio: PortfolioForEntry
    candidates: list[EntryCandidate]
    fees: FeeSchedule = Field(default_factory=FeeSchedule)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        codes = [candidate.code for candidate in self.candidates]
        if len(set(codes)) != len(codes):
            raise ValueError("新开仓候选股票代码不能重复")
        return self


class EntryCandidateDecision(BaseModel):
    code: str
    name: str
    route: CandidateRoute
    preliminary_rank: int
    base_grade: BaseOpportunityGrade
    grade: OpportunityGrade | None
    grade_points: int
    strong_evidence_bonus: int
    reward_risk_bonus: int
    net_reward_risk_ratio: float | None
    opinion_bonus: int
    ordinary_downgrade: int
    position_fraction: float
    open_allowed: bool
    block_reasons: list[str]
    fundamental_priority: FundamentalPriority


class SimulatedBuyOrder(BaseModel):
    actual_date: date
    strategy_version: str
    code: str
    name: str
    execution_price: float
    shares: int
    notional: float
    commission: float
    transfer_fee: float
    total_cash: float
    grade: OpportunityGrade
    position_fraction: float
    full_position_slot: float
    stop_price: float
    pressure_target: float | None
    initial_risk: float
    reason: Literal["divergence_entry"] = "divergence_entry"


class EntryPlanningDecision(BaseModel):
    actual_date: date
    strategy_version: str
    maximum_positions: int
    full_position_slot: float
    market_position_factor: float = Field(default=1, gt=0, le=1)
    candidates: list[EntryCandidateDecision]
    orders: list[SimulatedBuyOrder]
    remaining_cash: float

    @model_validator(mode="after")
    def validate_market_position_factor_version(self) -> Self:
        if (
            uses_v4_execution_rules(self.strategy_version)
            and "market_position_factor" not in self.model_fields_set
        ):
            raise ValueError("v4 及后续策略日必须显式记录市场仓位系数")
        return self


class OpinionTargetSelectionInput(BaseModel):
    """不使用舆论自身结果的每日调查目标排序输入。"""

    strategy_version: str = Field(
        default=CORE_STRATEGY_VERSION,
        min_length=1,
    )
    market_state: MarketState
    candidates: list[EntryCandidate]
    held_codes: list[str] = Field(default_factory=list)
    reference_full_position_slot: float = Field(default=10_000, gt=0)
    reference_available_cash: float | None = Field(default=None, ge=0)
    fees: FeeSchedule = Field(default_factory=FeeSchedule)
    limit: int = Field(default=10, ge=0, le=10)


def rank_candidates_for_opinion(
    source: OpinionTargetSelectionInput,
) -> list[str]:
    """
    按最终交易排序规则选择舆论目标，但强制移除舆论加减分。

    已持仓、停牌和跌停股票不占用新开仓舆论调查名额。
    """

    held_codes = set(source.held_codes)
    candidates = [
        candidate.model_copy(
            update={
                "favorable_opinion": False,
                "negative_opinion": False,
                "opinion_targeted": True,
            }
        )
        for candidate in source.candidates
        if candidate.code not in held_codes
        and not candidate.suspended
        and not candidate.at_limit_down
    ]
    if (
        uses_v4_execution_rules(source.strategy_version)
        and source.reference_available_cash is not None
    ):
        return _rank_opinion_candidates_with_cash(
            source,
            candidates=candidates,
            held_codes=held_codes,
        )
    decisions = {
        candidate.code: _evaluate_entry_candidate(
            candidate,
            strategy_version=source.strategy_version,
            market_state=source.market_state,
            market_data_complete=True,
            held_codes=held_codes,
            full_position_slot=source.reference_full_position_slot,
            maximum_affordable_shares=(
                _maximum_affordable_lot(
                    source.reference_available_cash,
                    price=candidate.close,
                    fees=source.fees,
                )
                if source.reference_available_cash is not None
                and uses_v4_execution_rules(source.strategy_version)
                else None
            ),
            fees=source.fees,
        )
        for candidate in candidates
    }
    candidates.sort(
        key=lambda candidate: _candidate_sort_key(
            candidate,
            decision=decisions[candidate.code],
            market_state=source.market_state,
        )
    )
    return [
        candidate.code
        for candidate in candidates[: source.limit]
    ]


def _rank_opinion_candidates_with_cash(
    source: OpinionTargetSelectionInput,
    *,
    candidates: list[EntryCandidate],
    held_codes: set[str],
) -> list[str]:
    remaining_cash = source.reference_available_cash
    assert remaining_cash is not None
    pending = list(candidates)
    ranked_codes: list[str] = []
    cash_committed = False
    while pending and len(ranked_codes) < source.limit:
        maximum_shares = {
            candidate.code: _maximum_affordable_lot(
                remaining_cash,
                price=candidate.close,
                fees=source.fees,
            )
            for candidate in pending
        }
        decisions = {
            candidate.code: _evaluate_entry_candidate(
                candidate,
                strategy_version=source.strategy_version,
                market_state=source.market_state,
                market_data_complete=True,
                held_codes=held_codes,
                full_position_slot=source.reference_full_position_slot,
                maximum_affordable_shares=maximum_shares[
                    candidate.code
                ],
                fees=source.fees,
            )
            for candidate in pending
        }
        pending.sort(
            key=lambda candidate: _candidate_sort_key(
                candidate,
                decision=decisions[candidate.code],
                market_state=source.market_state,
            )
        )
        candidate = pending.pop(0)
        decision = decisions[candidate.code]
        ranked_codes.append(candidate.code)
        if cash_committed or not decision.open_allowed:
            continue
        target_shares = _round_down_lot(
            (
                source.reference_full_position_slot
                * decision.position_fraction
            )
            / candidate.close
        )
        shares = min(
            target_shares,
            maximum_shares[candidate.code],
        )
        if shares < 100:
            continue
        target_cost = _buy_cost(
            target_shares,
            candidate.close,
            source.fees,
        )
        actual_cost = _buy_cost(
            shares,
            candidate.close,
            source.fees,
        )
        remaining_cash = round(remaining_cash - actual_cost, 2)
        if target_cost > actual_cost:
            cash_committed = True
    return ranked_codes


def plan_new_entries(source: EntryPlanningInput) -> EntryPlanningDecision:
    """
    评级、排序并生成一次性分歧买入订单。

    该接口内部统一处理评级上限、普通降级合计上限、
    基本面只作排序、
    固定持股数、整手、费用、现金不足和禁止加仓。
    """

    maximum_positions = _maximum_positions(source.portfolio.initial_capital)
    full_position_slot = source.portfolio.net_asset_value / maximum_positions
    held_codes = set(source.portfolio.held_codes)
    decisions = [
        _evaluate_entry_candidate(
            candidate,
            strategy_version=source.strategy_version,
            market_state=source.market_state,
            market_data_complete=source.market_data_complete,
            held_codes=held_codes,
            full_position_slot=full_position_slot,
            fees=source.fees,
        )
        for candidate in source.candidates
    ]
    decision_by_code = {item.code: item for item in decisions}
    candidate_by_code = {item.code: item for item in source.candidates}
    eligible = [
        candidate
        for candidate in source.candidates
        if decision_by_code[candidate.code].open_allowed
    ]
    eligible.sort(
        key=lambda candidate: _candidate_sort_key(
            candidate,
            decision=decision_by_code[candidate.code],
            market_state=source.market_state,
        )
    )

    available_slots = max(0, maximum_positions - len(held_codes))
    remaining_cash = source.portfolio.available_cash
    orders: list[SimulatedBuyOrder] = []
    cash_committed = False
    pending = list(eligible)
    while pending:
        if uses_v4_execution_rules(source.strategy_version):
            for pending_candidate in pending:
                decision_by_code[pending_candidate.code] = (
                    _evaluate_entry_candidate(
                        pending_candidate,
                        strategy_version=source.strategy_version,
                        market_state=source.market_state,
                        market_data_complete=source.market_data_complete,
                        held_codes=held_codes,
                        full_position_slot=full_position_slot,
                        maximum_affordable_shares=_maximum_affordable_lot(
                            remaining_cash,
                            price=pending_candidate.close,
                            fees=source.fees,
                        ),
                        fees=source.fees,
                    )
                )
            pending.sort(
                key=lambda pending_candidate: _candidate_sort_key(
                    pending_candidate,
                    decision=decision_by_code[pending_candidate.code],
                    market_state=source.market_state,
                )
            )
        candidate = pending.pop(0)
        decision = decision_by_code[candidate.code]
        if cash_committed:
            _block(decision, "cash_committed_to_higher_priority")
            continue
        if len(orders) >= available_slots:
            _block(decision, "maximum_positions_reached")
            continue
        assert decision.grade is not None
        target_notional = full_position_slot * decision.position_fraction
        target_shares = _round_down_lot(target_notional / candidate.close)
        if target_shares < 100:
            _block(decision, "target_below_one_lot")
            continue

        target_cost = _buy_cost(
            target_shares,
            candidate.close,
            source.fees,
        )
        used_cash_shortfall = target_cost > remaining_cash
        shares = (
            _maximum_affordable_lot(
                remaining_cash,
                price=candidate.close,
                fees=source.fees,
            )
            if used_cash_shortfall
            else target_shares
        )
        if shares < 100:
            _block(decision, "insufficient_cash_for_one_lot")
            continue

        order = _buy_order(
            source,
            candidate,
            decision,
            shares=shares,
            full_position_slot=full_position_slot,
        )
        orders.append(order)
        remaining_cash = round(remaining_cash - order.total_cash, 2)
        if used_cash_shortfall:
            # 现金不足目标仓位时，
            # 最高优先级候选获得全部可用现金。
            # 整手和费用留下的零头不再为低优先级候选继续分配。
            cash_committed = True

    # 输出顺序与调用者提交的初筛候选一致；
    # 订单另按执行优先级排列。
    ordered_decisions = [
        decision_by_code[candidate.code]
        for candidate in source.candidates
    ]
    return EntryPlanningDecision(
        actual_date=source.actual_date,
        strategy_version=source.strategy_version,
        maximum_positions=maximum_positions,
        full_position_slot=full_position_slot,
        market_position_factor=_market_position_factor(
            source.market_state,
            strategy_version=source.strategy_version,
        ),
        candidates=ordered_decisions,
        orders=orders,
        remaining_cash=remaining_cash,
    )


def _evaluate_entry_candidate(
    candidate: EntryCandidate,
    *,
    strategy_version: str,
    market_state: MarketState,
    market_data_complete: bool,
    held_codes: set[str],
    full_position_slot: float,
    maximum_affordable_shares: int | None = None,
    fees: FeeSchedule,
) -> EntryCandidateDecision:
    strong_bonus = min(len(candidate.strong_evidence_ids), 2)
    base_points = (
        _GRADE_POINTS[candidate.base_grade]
        + strong_bonus
        + (1 if candidate.favorable_opinion else 0)
        - int(
            candidate.standard_rsi_top_divergence
            or (
                market_state == "rising"
                and candidate.negative_opinion
            )
        )
    )
    reward_risk_ratio, reward_risk_bonus = _resolved_reward_risk(
        candidate,
        base_points=base_points,
        full_position_slot=full_position_slot,
        market_state=market_state,
        strategy_version=strategy_version,
        maximum_affordable_shares=maximum_affordable_shares,
        fees=fees,
    )
    opinion_bonus = 1 if candidate.favorable_opinion else 0
    ordinary_downgrade = int(
        candidate.standard_rsi_top_divergence
        or (market_state == "rising" and candidate.negative_opinion)
    )
    raw_points = base_points + reward_risk_bonus
    grade_points = min(raw_points, 3)
    grade = _POINTS_GRADE.get(grade_points)
    position_fraction = (
        _effective_position_fraction(
            grade,
            candidate.support_source_count,
            market_state=market_state,
            strategy_version=strategy_version,
        )
        if grade is not None
        else 0
    )
    reasons: list[str] = []
    if not market_data_complete:
        reasons.append("market_snapshot_incomplete")
    if not _route_allowed(candidate.route, market_state):
        reasons.append("route_not_allowed_for_market")
    if candidate.strong_bearish_veto or candidate.weak_bearish_rsi_composite_veto:
        reasons.append("bearish_veto")
    if grade is None:
        reasons.append("grade_below_c")
    if not candidate.entry_triggers:
        reasons.append("no_current_entry_trigger")
    if (
        candidate.stop_price is None
        or candidate.stop_price >= candidate.close
    ):
        reasons.append("no_executable_stop_line")
    if not candidate.opinion_targeted:
        reasons.append("not_in_opinion_targets")
    if candidate.suspended:
        reasons.append("suspended")
    if candidate.at_limit_down:
        reasons.append("limit_down")
    if candidate.code in held_codes:
        reasons.append("already_held_no_adding")

    return EntryCandidateDecision(
        code=candidate.code,
        name=candidate.name,
        route=candidate.route,
        preliminary_rank=candidate.preliminary_rank,
        base_grade=candidate.base_grade,
        grade=grade,
        grade_points=grade_points,
        strong_evidence_bonus=strong_bonus,
        reward_risk_bonus=reward_risk_bonus,
        net_reward_risk_ratio=reward_risk_ratio,
        opinion_bonus=opinion_bonus,
        ordinary_downgrade=ordinary_downgrade,
        position_fraction=position_fraction,
        open_allowed=not reasons,
        block_reasons=reasons,
        fundamental_priority=candidate.fundamental_priority,
    )


def _candidate_sort_key(
    candidate: EntryCandidate,
    *,
    decision: EntryCandidateDecision,
    market_state: MarketState,
) -> tuple[int, int, int, int, float, int, str]:
    route_priority = int(
        not (
            market_state == "rising"
            and candidate.route == "industry_chain"
        )
    )
    fundamental_priority = {
        "preferred": 0,
        "neutral": 1,
        "deprioritized": 2,
    }[candidate.fundamental_priority]
    price_priority = {
        "limit_up": 0,
        "big_bullish_candle": 1,
        "normal": 2,
    }[candidate.price_priority]
    reward_risk = (
        -decision.net_reward_risk_ratio
        if decision.net_reward_risk_ratio is not None
        else math.inf
    )
    return (
        -decision.grade_points,
        route_priority,
        fundamental_priority,
        price_priority,
        reward_risk,
        candidate.preliminary_rank,
        candidate.code,
    )


def _route_allowed(route: CandidateRoute, market_state: MarketState) -> bool:
    if market_state == "rising":
        return route in {"industry_chain", "technical_mainline"}
    return route == "pure_technical"


def _reward_risk_bonus(value: float | None) -> int:
    if value is None or value < 2:
        return 0
    if value < 3:
        return 1
    return 2


def _resolved_reward_risk(
    candidate: EntryCandidate,
    *,
    base_points: int,
    full_position_slot: float,
    market_state: MarketState,
    strategy_version: str,
    maximum_affordable_shares: int | None,
    fees: FeeSchedule,
) -> tuple[float | None, int]:
    if candidate.net_reward_risk_ratio is not None:
        supplied_value = candidate.net_reward_risk_ratio
        return supplied_value, _reward_risk_bonus(supplied_value)
    bonus = 0
    seen: list[int] = []
    projected_value: float | None = None
    for _ in range(4):
        grade = _POINTS_GRADE.get(min(base_points + bonus, 3))
        if grade is None:
            return None, 0
        fraction = _effective_position_fraction(
            grade,
            candidate.support_source_count,
            market_state=market_state,
            strategy_version=strategy_version,
        )
        projected_value = _projected_net_reward_risk(
            candidate,
            target_notional=full_position_slot * fraction,
            maximum_affordable_shares=maximum_affordable_shares,
            fees=fees,
        )
        next_bonus = _reward_risk_bonus(projected_value)
        if next_bonus == bonus:
            return projected_value, bonus
        seen.append(next_bonus)
        bonus = next_bonus
    conservative_bonus = min(seen, default=bonus)
    return projected_value, conservative_bonus


def _projected_net_reward_risk(
    candidate: EntryCandidate,
    *,
    target_notional: float,
    maximum_affordable_shares: int | None = None,
    fees: FeeSchedule,
) -> float | None:
    if (
        candidate.stop_price is None
        or candidate.pressure_target is None
        or candidate.stop_price >= candidate.close
        or candidate.pressure_target <= candidate.close
    ):
        return None
    shares = _round_down_lot(target_notional / candidate.close)
    if maximum_affordable_shares is not None:
        shares = min(shares, maximum_affordable_shares)
    if shares < 100:
        return None
    entry_cash = _buy_cost(shares, candidate.close, fees)
    stop_cash = _net_sell_cash(
        shares,
        price=candidate.stop_price,
        fees=fees,
    )
    target_cash = _net_sell_cash(
        shares,
        price=candidate.pressure_target,
        fees=fees,
    )
    risk = entry_cash - stop_cash
    reward = target_cash - entry_cash
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _net_sell_cash(
    shares: int,
    *,
    price: float,
    fees: FeeSchedule,
) -> float:
    notional = shares * price
    commission = max(
        fees.minimum_commission,
        notional * fees.commission_rate,
    )
    transfer_fee = notional * fees.transfer_fee_rate
    stamp_tax = notional * fees.stamp_tax_rate
    return notional - commission - transfer_fee - stamp_tax


def _position_fraction(
    grade: OpportunityGrade,
    support_source_count: int,
) -> float:
    support_bonus = (
        0.2
        if support_source_count >= 3
        else 0.1
        if support_source_count == 2
        else 0
    )
    return min(1, _POSITION_FRACTION[grade] + support_bonus)


def _market_position_factor(
    market_state: MarketState,
    *,
    strategy_version: str,
) -> float:
    if not uses_v4_execution_rules(strategy_version):
        return 1.0
    return _MARKET_POSITION_FACTOR[market_state]


def uses_v4_execution_rules(strategy_version: str) -> bool:
    prefix = "core-strategy-v"
    if not strategy_version.startswith(prefix):
        return False
    version_text = strategy_version.removeprefix(prefix)
    version_number_text = version_text.split("-", 1)[0]
    if not version_number_text.isdigit():
        return False
    version_number = int(version_number_text)
    return version_number >= 4


def _effective_position_fraction(
    grade: OpportunityGrade,
    support_source_count: int,
    *,
    market_state: MarketState,
    strategy_version: str,
) -> float:
    return (
        _position_fraction(grade, support_source_count)
        * _market_position_factor(
            market_state,
            strategy_version=strategy_version,
        )
    )


def _maximum_positions(initial_capital: float) -> int:
    if initial_capital < 50_000:
        return 1
    if initial_capital < 100_000:
        return 2
    if initial_capital < 150_000:
        return 3
    if initial_capital < 200_000:
        return 4
    return 5


def _round_down_lot(raw_shares: float) -> int:
    return math.floor(raw_shares / 100) * 100


def _buy_fees(
    shares: int,
    price: float,
    fees: FeeSchedule,
) -> tuple[float, float]:
    notional = shares * price
    commission = round(
        max(fees.minimum_commission, notional * fees.commission_rate),
        2,
    )
    transfer_fee = round(notional * fees.transfer_fee_rate, 2)
    return commission, transfer_fee


def _buy_cost(
    shares: int,
    price: float,
    fees: FeeSchedule,
) -> float:
    commission, transfer_fee = _buy_fees(shares, price, fees)
    return round(shares * price + commission + transfer_fee, 2)


def _maximum_affordable_lot(
    cash: float,
    *,
    price: float,
    fees: FeeSchedule,
) -> int:
    shares = _round_down_lot(cash / price)
    while shares >= 100 and _buy_cost(shares, price, fees) > cash:
        shares -= 100
    return shares


def _buy_order(
    source: EntryPlanningInput,
    candidate: EntryCandidate,
    decision: EntryCandidateDecision,
    *,
    shares: int,
    full_position_slot: float,
) -> SimulatedBuyOrder:
    assert decision.grade is not None
    assert candidate.stop_price is not None
    notional = round(shares * candidate.close, 2)
    commission, transfer_fee = _buy_fees(
        shares,
        candidate.close,
        source.fees,
    )
    return SimulatedBuyOrder(
        actual_date=source.actual_date,
        strategy_version=source.strategy_version,
        code=candidate.code,
        name=candidate.name,
        execution_price=candidate.close,
        shares=shares,
        notional=notional,
        commission=commission,
        transfer_fee=transfer_fee,
        total_cash=round(notional + commission + transfer_fee, 2),
        grade=decision.grade,
        position_fraction=decision.position_fraction,
        full_position_slot=full_position_slot,
        stop_price=candidate.stop_price,
        pressure_target=candidate.pressure_target,
        initial_risk=round(
            candidate.close - candidate.stop_price,
            6,
        ),
    )


def _block(decision: EntryCandidateDecision, reason: str) -> None:
    if reason not in decision.block_reasons:
        decision.block_reasons.append(reason)
    decision.open_allowed = False
