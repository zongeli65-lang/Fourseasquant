from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from fourseasquant.core_strategy import (
    CandidatePipelineDecision,
    CandidatePipelineInput,
    FundamentalPriority,
    FundamentalInvestigation,
    evaluate_candidate_pipeline,
)
from fourseasquant.core_strategy_adapters import (
    CoreStrategyDailyInputs,
    PreparedStrategyCandidate,
    StrategyInputUnavailable,
    StrategyOpinionEvidence,
    load_core_strategy_daily_inputs,
    load_position_observations,
)
from fourseasquant.core_strategy_execution import (
    EntryCandidate,
    BaseOpportunityGrade,
    EntryPlanningDecision,
    EntryPlanningInput,
    FeeSchedule,
    OpinionTargetSelectionInput,
    PortfolioForEntry,
    PricePriority,
    plan_new_entries,
    rank_candidates_for_opinion,
)
from fourseasquant.core_strategy_positions import (
    HoldingPosition,
    PositionManagementDecision,
    PositionManagementInput,
    PositionObservation,
    manage_positions,
)
from fourseasquant.core_strategy_repository import (
    CoreStrategyDaySnapshot,
    CoreStrategyPortfolioSnapshot,
    PortfolioPositionMark,
    PublishedCoreStrategyDaySnapshot,
    PublishedCoreStrategyPortfolioSnapshot,
    StrategyFundamentalTargetSnapshot,
    create_core_strategy_tables,
    publish_core_strategy_day,
    publish_core_strategy_portfolio,
    publish_strategy_fundamental_targets,
)
from fourseasquant.public_opinion_repository import (
    StrategyOpinionTargetSnapshot,
    create_public_opinion_tables,
    publish_strategy_opinion_targets,
)


class StrategyInvestigationPreparation(BaseModel):
    daily_inputs: CoreStrategyDailyInputs
    candidate_pipeline: CandidatePipelineDecision
    opinion_targets: StrategyOpinionTargetSnapshot
    fundamental_targets: StrategyFundamentalTargetSnapshot


class FinalizedStrategyDay(BaseModel):
    published: PublishedCoreStrategyDaySnapshot
    ending_positions: list[HoldingPosition]
    ending_cash: float
    portfolio_publication_status: Literal[
        "published",
        "blocked_missing_marks",
    ]
    portfolio: PublishedCoreStrategyPortfolioSnapshot | None = None
    portfolio_publication_blocked_codes: list[str] = Field(
        default_factory=list
    )


def prepare_strategy_investigations(
    path: Path,
    *,
    requested_date: date,
    strategy_version: str,
    corporate_actions_complete: bool,
    portfolio: PortfolioForEntry,
    fundamental_investigations: Mapping[
        str,
        FundamentalInvestigation,
    ] | None,
    published_at: datetime,
    fees: FeeSchedule | None = None,
) -> StrategyInvestigationPreparation:
    """
    构造初筛候选，并发布后置基本面和舆论调查目标。

    该阶段不生成模拟订单；调查结果不会倒灌改写初筛候选。
    """

    if published_at.tzinfo is None:
        raise ValueError("调查目标发布时间必须包含时区")
    daily = load_core_strategy_daily_inputs(
        path,
        requested_date=requested_date,
        corporate_actions_complete=corporate_actions_complete,
    )
    investigations = fundamental_investigations or {}
    preliminary = [
        item.preliminary.model_copy(
            update={
                "fundamental": investigations.get(
                    item.preliminary.code
                )
            }
        )
        for item in daily.candidates
    ]
    pipeline = evaluate_candidate_pipeline(
        CandidatePipelineInput(
            actual_date=daily.actual_date,
            strategy_version=strategy_version,
            candidates=preliminary,
        )
    )
    provisional = _entry_candidates(
        daily,
        pipeline=pipeline,
        opinions={},
    )
    opinion_codes = rank_candidates_for_opinion(
        OpinionTargetSelectionInput(
            market_state=daily.market_state,
            candidates=provisional,
            held_codes=portfolio.held_codes,
            reference_full_position_slot=(
                portfolio.net_asset_value
                / _maximum_positions(portfolio.initial_capital)
            ),
            fees=fees or FeeSchedule(),
        )
    )
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        create_public_opinion_tables(connection)
    fundamental_snapshot = publish_strategy_fundamental_targets(
        path,
        actual_date=daily.actual_date,
        strategy_version=strategy_version,
        targets=pipeline.fundamental_targets,
        published_at=published_at,
    )
    opinion_snapshot = publish_strategy_opinion_targets(
        path,
        actual_date=daily.actual_date,
        strategy_version=strategy_version,
        codes=opinion_codes,
        published_at=published_at,
    )
    return StrategyInvestigationPreparation(
        daily_inputs=daily,
        candidate_pipeline=pipeline,
        opinion_targets=opinion_snapshot,
        fundamental_targets=fundamental_snapshot,
    )


def finalize_strategy_day(
    path: Path,
    *,
    preparation: StrategyInvestigationPreparation,
    opinions: list[StrategyOpinionEvidence],
    portfolio: PortfolioForEntry,
    positions: list[HoldingPosition],
    published_at: datetime,
    observations: list[PositionObservation] | None = None,
    fees: FeeSchedule | None = None,
) -> FinalizedStrategyDay:
    """完成持仓退出、新开仓、结果组合和不可变发布。"""

    daily = preparation.daily_inputs
    strategy_version = preparation.opinion_targets.strategy_version
    if not daily.corporate_actions_complete:
        raise StrategyInputUnavailable(
            "公司行为数据不完整，策略日结果禁止发布"
        )
    opinion_by_code = {item.code: item for item in opinions}
    expected_codes = {
        item.preliminary.code
        for item in daily.candidates
    }
    if set(opinion_by_code) != expected_codes:
        raise ValueError("舆论证据必须逐只覆盖同日初筛候选")
    if {
        code
        for code, evidence in opinion_by_code.items()
        if evidence.targeted
    } != set(preparation.opinion_targets.codes):
        raise ValueError("舆论证据的调查目标必须与已发布快照一致")

    fee_schedule = fees or FeeSchedule()
    position_observations = (
        observations
        if observations is not None
        else load_position_observations(
            path,
            daily_inputs=daily,
            positions=positions,
        ).observations
    )
    position_decision = manage_positions(
        PositionManagementInput(
            actual_date=daily.actual_date,
            strategy_version=strategy_version,
            corporate_actions_complete=True,
            positions=positions,
            observations=position_observations,
            fees=fee_schedule,
        )
    )
    cash_after_exits = round(
        portfolio.available_cash
        + sum(order.net_cash for order in position_decision.orders),
        2,
    )
    surviving_codes = [
        position.code
        for position in position_decision.positions
    ]
    entry_decision = plan_new_entries(
        EntryPlanningInput(
            actual_date=daily.actual_date,
            strategy_version=strategy_version,
            market_state=daily.market_state,
            market_data_complete=daily.market_data_complete,
            portfolio=portfolio.model_copy(
                update={
                    "available_cash": cash_after_exits,
                    "held_codes": surviving_codes,
                }
            ),
            candidates=_entry_candidates(
                daily,
                pipeline=preparation.candidate_pipeline,
                opinions=opinion_by_code,
            ),
            fees=fee_schedule,
        )
    )
    snapshot = CoreStrategyDaySnapshot(
        actual_date=daily.actual_date,
        strategy_version=strategy_version,
        market_state=daily.market_state,
        input_versions=daily.input_versions.model_copy(
            update={
                "fundamental": _fundamental_input_version(
                    preparation.candidate_pipeline
                ),
                "public_opinion": _opinion_input_version(opinions),
            }
        ),
        candidate_pipeline=preparation.candidate_pipeline,
        entry_planning=entry_decision,
        position_management=position_decision,
    )
    published = publish_core_strategy_day(
        path,
        snapshot=snapshot,
        published_at=published_at,
    )
    ending_positions = [
        *position_decision.positions,
        *[_new_position(order) for order in entry_decision.orders],
    ]
    (
        portfolio_publication_status,
        published_portfolio,
        missing_mark_codes,
    ) = _publish_ending_portfolio(
        path,
        actual_date=daily.actual_date,
        strategy_version=strategy_version,
        initial_capital=portfolio.initial_capital,
        available_cash=entry_decision.remaining_cash,
        positions=ending_positions,
        observations=position_observations,
        entry_decision=entry_decision,
        published_at=published_at,
    )
    return FinalizedStrategyDay(
        published=published,
        ending_positions=ending_positions,
        ending_cash=entry_decision.remaining_cash,
        portfolio_publication_status=portfolio_publication_status,
        portfolio=published_portfolio,
        portfolio_publication_blocked_codes=missing_mark_codes,
    )


def _publish_ending_portfolio(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str,
    initial_capital: float,
    available_cash: float,
    positions: list[HoldingPosition],
    observations: list[PositionObservation],
    entry_decision: EntryPlanningDecision,
    published_at: datetime,
) -> tuple[
    Literal["published", "blocked_missing_marks"],
    PublishedCoreStrategyPortfolioSnapshot | None,
    list[str],
]:
    observation_by_code = {
        observation.code: observation
        for observation in observations
    }
    order_by_code = {
        order.code: order
        for order in entry_decision.orders
    }
    marks: list[PortfolioPositionMark] = []
    missing_codes: list[str] = []
    for position in positions:
        order = order_by_code.get(position.code)
        if order is not None:
            marks.append(
                PortfolioPositionMark(
                    code=position.code,
                    price=order.execution_price,
                    mark_date=actual_date,
                    source="entry_execution",
                )
            )
            continue
        observation = observation_by_code.get(position.code)
        if observation is None:
            missing_codes.append(position.code)
            continue
        marks.append(
            PortfolioPositionMark(
                code=position.code,
                price=observation.close,
                mark_date=actual_date,
                source="daily_close",
            )
        )
    if missing_codes:
        return (
            "blocked_missing_marks",
            None,
            sorted(missing_codes),
        )
    mark_by_code = {mark.code: mark.price for mark in marks}
    net_asset_value = round(
        available_cash
        + sum(
            position.shares * mark_by_code[position.code]
            for position in positions
        ),
        2,
    )
    portfolio_snapshot = CoreStrategyPortfolioSnapshot(
        actual_date=actual_date,
        strategy_version=strategy_version,
        initial_capital=initial_capital,
        available_cash=available_cash,
        net_asset_value=net_asset_value,
        positions=positions,
        marks=marks,
    )
    return (
        "published",
        publish_core_strategy_portfolio(
            path,
            snapshot=portfolio_snapshot,
            published_at=published_at,
        ),
        [],
    )


def _entry_candidates(
    daily: CoreStrategyDailyInputs,
    *,
    pipeline: CandidatePipelineDecision,
    opinions: Mapping[str, StrategyOpinionEvidence],
) -> list[EntryCandidate]:
    evidence_by_code = {
        item.code: item
        for item in pipeline.candidates
    }
    return [
        _entry_candidate(
            item,
            fundamental_priority=evidence_by_code[
                item.preliminary.code
            ].fundamental_priority,
            opinion=opinions.get(item.preliminary.code),
        )
        for item in daily.candidates
    ]


def _entry_candidate(
    item: PreparedStrategyCandidate,
    *,
    fundamental_priority: FundamentalPriority,
    opinion: StrategyOpinionEvidence | None,
) -> EntryCandidate:
    technical = item.technical
    raw = item.raw_bar
    stop = technical.stop_price if technical is not None else None
    target = (
        technical.pressure_target
        if technical is not None
        else None
    )
    return EntryCandidate(
        code=item.preliminary.code,
        name=item.preliminary.name,
        route=item.preliminary.route,
        base_grade=cast(
            BaseOpportunityGrade,
            item.preliminary.base_grade,
        ),
        preliminary_rank=item.preliminary.preliminary_rank,
        entry_triggers=(
            technical.entry_triggers
            if technical is not None
            else []
        ),
        strong_evidence_ids=(
            technical.strong_evidence_ids
            if technical is not None
            else []
        ),
        net_reward_risk_ratio=None,
        favorable_opinion=(
            opinion is not None and opinion.status == "favorable"
        ),
        negative_opinion=(
            opinion is not None and opinion.status == "unfavorable"
        ),
        standard_rsi_top_divergence=(
            technical.standard_rsi_top_divergence_active
            if technical is not None
            else False
        ),
        strong_bearish_veto=(
            technical.strong_bearish_veto
            if technical is not None
            else False
        ),
        weak_bearish_rsi_composite_veto=(
            technical.weak_bearish_rsi_composite_veto
            if technical is not None
            else False
        ),
        opinion_targeted=opinion is not None and opinion.targeted,
        fundamental_priority=fundamental_priority,
        price_priority=_price_priority(item),
        support_source_count=(
            technical.supports[0].source_count
            if technical is not None and technical.supports
            else 0
        ),
        close=raw.close,
        stop_price=stop,
        pressure_target=target,
        suspended=raw.volume == 0,
        at_limit_down=raw.close <= _limit_price(
            raw.previous_close,
            multiplier="0.90",
        ),
    )


def _price_priority(
    item: PreparedStrategyCandidate,
) -> PricePriority:
    raw = item.raw_bar
    if raw.close >= _limit_price(
        raw.previous_close,
        multiplier="1.10",
    ):
        return "limit_up"
    if (
        item.technical is not None
        and item.technical.big_bullish_candle
    ):
        return "big_bullish_candle"
    return "normal"


def _limit_price(previous_close: float, *, multiplier: str) -> float:
    return float(
        (
            Decimal(str(previous_close))
            * Decimal(multiplier)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def _maximum_positions(initial_capital: float) -> int:
    return min(5, max(1, int(initial_capital // 50_000) + 1))


def _new_position(order: object) -> HoldingPosition:
    from fourseasquant.core_strategy_execution import SimulatedBuyOrder

    buy = SimulatedBuyOrder.model_validate(order)
    return HoldingPosition(
        code=buy.code,
        name=buy.name,
        shares=buy.shares,
        cost_price=buy.execution_price,
        stop_price=buy.stop_price,
        pressure_target=buy.pressure_target,
        initial_risk=buy.initial_risk,
        highest_close_since_entry=buy.execution_price,
    )


def _fundamental_input_version(
    pipeline: CandidatePipelineDecision,
) -> str:
    values = [
        (
            item.code,
            item.fundamental_state,
            item.fundamental_as_of_date.isoformat()
            if item.fundamental_as_of_date is not None
            else "",
        )
        for item in pipeline.candidates
    ]
    digest = hashlib.sha256(repr(values).encode()).hexdigest()
    return f"fundamental-evidence:{digest}"


def _opinion_input_version(
    opinions: list[StrategyOpinionEvidence],
) -> str:
    values = [
        item.model_dump(mode="json")
        for item in sorted(opinions, key=lambda value: value.code)
    ]
    digest = hashlib.sha256(repr(values).encode()).hexdigest()
    return f"opinion-evidence:{digest}"
