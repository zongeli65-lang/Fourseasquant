from datetime import date

import pytest
from pydantic import ValidationError

from fourseasquant.core_strategy import (
    CORE_STRATEGY_VERSION,
    CandidateRoute,
    FundamentalPriority,
)
from fourseasquant.core_strategy_execution import (
    BaseOpportunityGrade,
    EntryCandidate,
    EntryPlanningDecision,
    EntryPlanningInput,
    EntryTrigger,
    FeeSchedule,
    MarketState,
    PortfolioForEntry,
    PricePriority,
    OpinionTargetSelectionInput,
    plan_new_entries,
    rank_candidates_for_opinion,
)


TARGET_DATE = date(2026, 7, 29)


def _candidate(
    code: str = "600000",
    *,
    route: CandidateRoute = "technical_mainline",
    base_grade: BaseOpportunityGrade = "B",
    preliminary_rank: int = 1,
    entry_triggers: list[EntryTrigger] | None = None,
    strong_evidence_ids: list[str] | None = None,
    net_reward_risk_ratio: float | None = None,
    favorable_opinion: bool = False,
    negative_opinion: bool = False,
    standard_rsi_top_divergence: bool = False,
    strong_bearish_veto: bool = False,
    weak_bearish_rsi_composite_veto: bool = False,
    opinion_targeted: bool = True,
    fundamental_priority: FundamentalPriority = "neutral",
    price_priority: PricePriority = "normal",
    support_source_count: int = 1,
    close: float = 10,
    stop_price: float | None = None,
    pressure_target: float | None = None,
    suspended: bool = False,
    at_limit_down: bool = False,
) -> EntryCandidate:
    return EntryCandidate(
        code=code,
        name=f"股票{code}",
        route=route,
        base_grade=base_grade,
        preliminary_rank=preliminary_rank,
        entry_triggers=(
            ["support_retest"] if entry_triggers is None else entry_triggers
        ),
        strong_evidence_ids=(
            [] if strong_evidence_ids is None else strong_evidence_ids
        ),
        net_reward_risk_ratio=net_reward_risk_ratio,
        favorable_opinion=favorable_opinion,
        negative_opinion=negative_opinion,
        standard_rsi_top_divergence=standard_rsi_top_divergence,
        strong_bearish_veto=strong_bearish_veto,
        weak_bearish_rsi_composite_veto=weak_bearish_rsi_composite_veto,
        opinion_targeted=opinion_targeted,
        fundamental_priority=fundamental_priority,
        price_priority=price_priority,
        support_source_count=support_source_count,
        close=close,
        stop_price=(
            max(0.01, round(close * 0.8, 2))
            if stop_price is None
            else stop_price
        ),
        pressure_target=(
            round(close * 1.4, 2)
            if pressure_target is None
            else pressure_target
        ),
        suspended=suspended,
        at_limit_down=at_limit_down,
    )


def _plan(
    *candidates: EntryCandidate,
    market_state: MarketState = "rising",
    market_data_complete: bool = True,
    initial_capital: float = 40_000,
    net_asset_value: float = 40_000,
    available_cash: float = 40_000,
    held_codes: list[str] | None = None,
    fees: FeeSchedule | None = None,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> EntryPlanningDecision:
    return plan_new_entries(
        EntryPlanningInput(
            actual_date=TARGET_DATE,
            strategy_version=strategy_version,
            market_state=market_state,
            market_data_complete=market_data_complete,
            portfolio=PortfolioForEntry(
                initial_capital=initial_capital,
                net_asset_value=net_asset_value,
                available_cash=available_cash,
                held_codes=held_codes or [],
            ),
            candidates=list(candidates),
            fees=fees or FeeSchedule(),
        )
    )


def test_grade_uses_strong_evidence_rr_and_opinion_without_fundamental() -> None:
    ordinary = _candidate(
        code="600000",
        strong_evidence_ids=["strong_bullish_candle"],
        net_reward_risk_ratio=3,
        favorable_opinion=True,
        fundamental_priority="deprioritized",
    )
    hard = _candidate(
        code="600001",
        strong_evidence_ids=["strong_bullish_candle"],
        net_reward_risk_ratio=3,
        favorable_opinion=True,
        fundamental_priority="preferred",
        preliminary_rank=2,
    )

    decision = _plan(ordinary, hard, initial_capital=100_000)

    assert [item.grade for item in decision.candidates] == ["S", "S"]
    assert [item.position_fraction for item in decision.candidates] == [1, 1]
    assert [order.code for order in decision.orders] == ["600001", "600000"]


def test_unsupplied_reward_risk_is_net_of_both_side_fees() -> None:
    candidate = _candidate(
        base_grade="C",
        net_reward_risk_ratio=None,
        close=10,
    )

    item = _plan(candidate).candidates[0]

    assert item.net_reward_risk_ratio is not None
    assert item.net_reward_risk_ratio < 2
    assert item.reward_risk_bonus == 0
    assert item.grade == "C"


def test_two_independent_strong_evidence_items_add_at_most_two_levels() -> None:
    candidate = _candidate(
        base_grade="C",
        strong_evidence_ids=["bullish_engulfing", "standard_rsi_bottom_divergence"],
    )

    item = _plan(candidate).candidates[0]

    assert item.grade == "A"
    assert item.grade_points == 2


def test_ordinary_downgrades_have_a_combined_cap_of_one_level() -> None:
    candidate = _candidate(
        base_grade="B",
        standard_rsi_top_divergence=True,
        negative_opinion=True,
    )

    item = _plan(candidate).candidates[0]

    assert item.grade == "C"
    assert item.ordinary_downgrade == 1


def test_negative_opinion_is_ignored_outside_rising_market() -> None:
    candidate = _candidate(
        route="pure_technical",
        base_grade="C",
        negative_opinion=True,
    )

    item = _plan(candidate, market_state="falling").candidates[0]

    assert item.grade == "C"
    assert item.ordinary_downgrade == 0
    assert item.open_allowed is True


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(strong_bearish_veto=True),
        _candidate(weak_bearish_rsi_composite_veto=True),
    ],
)
def test_direct_bearish_veto_cancels_entry(candidate: EntryCandidate) -> None:
    item = _plan(candidate).candidates[0]

    assert item.open_allowed is False
    assert "bearish_veto" in item.block_reasons


def test_old_quality_evidence_without_fresh_trigger_cannot_open() -> None:
    candidate = _candidate(
        entry_triggers=[],
        strong_evidence_ids=["strong_bullish_from_three_days_ago"],
    )

    item = _plan(candidate).candidates[0]

    assert item.grade == "A"
    assert item.open_allowed is False
    assert "no_current_entry_trigger" in item.block_reasons


def test_only_published_opinion_targets_can_open_that_day() -> None:
    item = _plan(_candidate(opinion_targeted=False)).candidates[0]

    assert item.open_allowed is False
    assert "not_in_opinion_targets" in item.block_reasons


def test_incomplete_market_snapshot_blocks_new_orders() -> None:
    decision = _plan(_candidate(), market_data_complete=False)

    assert decision.orders == []
    assert "market_snapshot_incomplete" in decision.candidates[0].block_reasons


def test_existing_position_is_never_added_to() -> None:
    decision = _plan(_candidate(), held_codes=["600000"])

    assert decision.orders == []
    assert "already_held_no_adding" in decision.candidates[0].block_reasons


@pytest.mark.parametrize(
    ("grade", "expected_fraction"),
    [("S", 1), ("A", 0.75), ("B", 0.5), ("C", 0.25)],
)
def test_grade_maps_to_one_time_target_position(
    grade: str,
    expected_fraction: float,
) -> None:
    strong_count = {"S": 2, "A": 2, "B": 1, "C": 0}[grade]
    candidate = _candidate(
        base_grade="C",
        strong_evidence_ids=[f"strong-{index}" for index in range(strong_count)],
        net_reward_risk_ratio=2 if grade == "S" else None,
    )

    item = _plan(candidate).candidates[0]

    assert item.grade == grade
    assert item.position_fraction == expected_fraction


@pytest.mark.parametrize(
    ("market_state", "expected_factor", "expected_fraction"),
    [
        ("rising", 1.0, 1.0),
        ("sideways", 0.5, 0.5),
        ("falling", 0.2, 0.2),
    ],
)
def test_market_state_scales_s_grade_position_without_changing_grade(
    market_state: MarketState,
    expected_factor: float,
    expected_fraction: float,
) -> None:
    candidate = _candidate(
        route=(
            "technical_mainline"
            if market_state == "rising"
            else "pure_technical"
        ),
        strong_evidence_ids=["strong-candle", "strong-rsi"],
    )

    decision = _plan(candidate, market_state=market_state)

    assert decision.market_position_factor == expected_factor
    assert decision.candidates[0].grade == "S"
    assert decision.candidates[0].position_fraction == expected_fraction
    assert decision.orders[0].position_fraction == expected_fraction


def test_v3_replay_keeps_pre_market_scaling_position_semantics() -> None:
    candidate = _candidate(
        route="pure_technical",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
    )

    decision = _plan(
        candidate,
        market_state="sideways",
        strategy_version="core-strategy-v3",
    )

    assert decision.market_position_factor == 1.0
    assert decision.candidates[0].position_fraction == 1.0
    assert decision.orders[0].position_fraction == 1.0


def test_v4_snapshot_rejects_a_missing_market_position_factor() -> None:
    with pytest.raises(ValidationError, match="必须显式记录"):
        EntryPlanningDecision(
            actual_date=TARGET_DATE,
            strategy_version="core-strategy-v4",
            maximum_positions=1,
            full_position_slot=40_000,
            candidates=[],
            orders=[],
            remaining_cash=40_000,
        )


def test_market_factor_applies_after_merged_support_bonus() -> None:
    two_sources = _candidate(
        code="600000",
        route="pure_technical",
        support_source_count=2,
    )
    three_sources = _candidate(
        code="600001",
        route="pure_technical",
        support_source_count=3,
        preliminary_rank=2,
    )

    decision = _plan(
        two_sources,
        three_sources,
        market_state="sideways",
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=100_000,
    )

    assert [item.grade for item in decision.candidates] == ["B", "B"]
    assert [item.position_fraction for item in decision.candidates] == [
        0.3,
        0.35,
    ]


def test_net_reward_risk_uses_market_adjusted_order_size() -> None:
    candidate = _candidate(
        route="pure_technical",
        base_grade="C",
        net_reward_risk_ratio=None,
        close=10,
    )

    decision = _plan(candidate, market_state="sideways")

    assert decision.candidates[0].grade == "C"
    assert decision.orders[0].shares == 500
    entry_cash = 5_005.05
    stop_cash = 3_992.96
    target_cash = 6_991.43
    assert decision.candidates[0].net_reward_risk_ratio == pytest.approx(
        (target_cash - entry_cash) / (entry_cash - stop_cash)
    )


@pytest.mark.parametrize(
    ("market_state", "expected_factor"),
    [
        ("rising", 1.0),
        ("sideways", 0.5),
        ("falling", 0.2),
    ],
)
def test_market_state_caps_aggregate_new_position_notional(
    market_state: MarketState,
    expected_factor: float,
) -> None:
    candidates = [
        _candidate(
            f"{600000 + index:06d}",
            route=(
                "technical_mainline"
                if market_state == "rising"
                else "pure_technical"
            ),
            strong_evidence_ids=["strong-candle", "strong-rsi"],
            preliminary_rank=index + 1,
            close=10,
        )
        for index in range(5)
    ]

    decision = _plan(
        *candidates,
        market_state=market_state,
        initial_capital=200_000,
        net_asset_value=200_000,
        available_cash=200_000,
    )

    assert len(decision.orders) == 5
    assert sum(order.notional for order in decision.orders) <= (
        decision.full_position_slot
        * decision.maximum_positions
        * expected_factor
    )


def test_merged_strong_support_only_increases_initial_position_fraction() -> None:
    two_sources = _candidate(
        code="600000",
        base_grade="B",
        support_source_count=2,
    )
    three_sources = _candidate(
        code="600001",
        base_grade="B",
        support_source_count=3,
        preliminary_rank=2,
    )

    decision = _plan(two_sources, three_sources, initial_capital=100_000)

    assert [item.grade for item in decision.candidates] == ["B", "B"]
    assert [item.position_fraction for item in decision.candidates] == [0.6, 0.7]


def test_order_reserves_fees_and_uses_hundred_share_lots() -> None:
    fees = FeeSchedule(
        commission_rate=0.00025,
        minimum_commission=5,
        transfer_fee_rate=0.00001,
        stamp_tax_rate=0.0005,
    )

    decision = _plan(
        _candidate(base_grade="B", close=10),
        fees=fees,
    )

    order = decision.orders[0]
    assert order.shares == 2_000
    assert order.notional == pytest.approx(20_000)
    assert order.commission == pytest.approx(5)
    assert order.transfer_fee == pytest.approx(0.2)
    assert order.total_cash == pytest.approx(20_005.2)
    assert decision.remaining_cash == pytest.approx(19_994.8)


def test_cash_shortfall_uses_all_affordable_cash_for_highest_priority() -> None:
    high = _candidate(
        code="600000",
        base_grade="B",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
        close=10,
    )
    low = _candidate(
        code="600001",
        base_grade="B",
        preliminary_rank=2,
        close=5,
    )

    decision = _plan(
        high,
        low,
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=12_000,
    )

    assert [order.code for order in decision.orders] == ["600000"]
    assert decision.orders[0].shares == 1_100
    assert decision.remaining_cash < 1_000


def test_v4_cash_shortfall_rr_uses_actual_affordable_shares() -> None:
    candidate = _candidate(
        base_grade="B",
        close=10,
        stop_price=9.95,
        pressure_target=10.14,
    )

    decision = _plan(
        candidate,
        initial_capital=40_000,
        net_asset_value=40_000,
        available_cash=4_000,
    )

    item = decision.candidates[0]
    order = decision.orders[0]
    assert order.shares == 300
    assert item.net_reward_risk_ratio == pytest.approx(
        1.1456078275557395
    )
    assert item.reward_risk_bonus == 0
    assert item.grade == "B"


def test_v4_recalculates_later_candidate_after_previous_order() -> None:
    first = _candidate(
        code="600000",
        base_grade="B",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
        net_reward_risk_ratio=0,
        close=10,
    )
    second = _candidate(
        code="600001",
        base_grade="B",
        preliminary_rank=2,
        close=10,
        stop_price=9.95,
        pressure_target=10.14,
    )

    decision = _plan(
        first,
        second,
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=40_000,
    )

    assert [order.code for order in decision.orders] == [
        "600000",
        "600001",
    ]
    assert decision.orders[1].shares == 600
    second_decision = decision.candidates[1]
    assert second_decision.net_reward_risk_ratio == pytest.approx(
        1.6433743884077456
    )
    assert second_decision.reward_risk_bonus == 0
    assert second_decision.grade == "B"


def test_v4_reorders_remaining_candidates_after_cash_changes() -> None:
    first = _candidate(
        code="600000",
        base_grade="B",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
        net_reward_risk_ratio=0,
        close=10,
    )
    cash_sensitive = _candidate(
        code="600001",
        base_grade="B",
        preliminary_rank=2,
        close=10,
        stop_price=9.95,
        pressure_target=10.14,
    )
    stable = _candidate(
        code="600002",
        base_grade="B",
        preliminary_rank=3,
        net_reward_risk_ratio=0,
        fundamental_priority="preferred",
        close=10,
    )

    decision = _plan(
        first,
        cash_sensitive,
        stable,
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=40_000,
    )

    assert [order.code for order in decision.orders] == [
        "600000",
        "600002",
    ]
    sensitive_decision = decision.candidates[1]
    assert sensitive_decision.grade == "B"
    assert "cash_committed_to_higher_priority" in (
        sensitive_decision.block_reasons
    )


def test_v3_cash_shortfall_keeps_legacy_reward_risk_semantics() -> None:
    candidate = _candidate(
        base_grade="B",
        close=10,
        stop_price=9.95,
        pressure_target=10.14,
    )

    decision = _plan(
        candidate,
        initial_capital=40_000,
        net_asset_value=40_000,
        available_cash=4_000,
        strategy_version="core-strategy-v3",
    )

    item = decision.candidates[0]
    assert decision.orders[0].shares == 300
    assert item.net_reward_risk_ratio == pytest.approx(
        2.155739503340945
    )
    assert item.reward_risk_bonus == 1
    assert item.grade == "A"


def test_too_expensive_candidate_is_skipped_for_cheaper_candidate() -> None:
    expensive = _candidate(
        code="600000",
        base_grade="B",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
        close=200,
    )
    cheap = _candidate(
        code="600001",
        base_grade="B",
        preliminary_rank=2,
        close=10,
    )

    decision = _plan(
        expensive,
        cheap,
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=1_500,
    )

    assert [order.code for order in decision.orders] == ["600001"]
    assert decision.orders[0].shares == 100


def test_maximum_position_count_is_fixed_by_initial_capital() -> None:
    candidates = [
        _candidate(
            f"{600000 + index:06d}",
            base_grade="B",
            strong_evidence_ids=["strong-candle", "strong-rsi"],
            preliminary_rank=index + 1,
            close=1,
        )
        for index in range(6)
    ]

    decision = _plan(
        *candidates,
        initial_capital=200_000,
        net_asset_value=200_000,
        available_cash=200_000,
    )

    assert decision.maximum_positions == 5
    assert len(decision.orders) == 5


def test_suspension_and_limit_down_block_buy_but_limit_up_is_allowed() -> None:
    suspended = _candidate(code="600000", suspended=True)
    limit_down = _candidate(code="600001", at_limit_down=True, preliminary_rank=2)
    limit_up = _candidate(
        code="600002",
        price_priority="limit_up",
        preliminary_rank=3,
    )

    decision = _plan(
        suspended,
        limit_down,
        limit_up,
        initial_capital=100_000,
    )

    assert [order.code for order in decision.orders] == ["600002"]


def test_opinion_targets_ignore_opinion_itself_and_exclude_untradeable() -> None:
    higher_quality = _candidate(
        code="600001",
        base_grade="B",
        strong_evidence_ids=["strong-candle"],
        preliminary_rank=2,
    )
    favorable_but_lower = _candidate(
        code="600002",
        base_grade="C",
        favorable_opinion=True,
        preliminary_rank=1,
    )
    held = _candidate(
        code="600003",
        base_grade="B",
        strong_evidence_ids=["strong-candle", "strong-rsi"],
    )
    suspended = _candidate(
        code="600004",
        suspended=True,
    )

    codes = rank_candidates_for_opinion(
        OpinionTargetSelectionInput(
            market_state="rising",
            candidates=[
                favorable_but_lower,
                higher_quality,
                held,
                suspended,
            ],
            held_codes=["600003"],
        )
    )

    assert codes == ["600001", "600002"]


def test_v4_opinion_targets_use_the_same_available_cash_as_entry_ranking() -> None:
    cash_sensitive = [
        _candidate(
            code=f"{600000 + index:06d}",
            base_grade="B",
            preliminary_rank=index + 1,
            close=10,
            stop_price=9.95,
            pressure_target=10.14,
        )
        for index in range(10)
    ]
    stable = _candidate(
        code="600010",
        base_grade="B",
        preliminary_rank=11,
        net_reward_risk_ratio=0,
        fundamental_priority="preferred",
        close=10,
    )

    codes = rank_candidates_for_opinion(
        OpinionTargetSelectionInput(
            strategy_version=CORE_STRATEGY_VERSION,
            market_state="rising",
            candidates=[*cash_sensitive, stable],
            reference_full_position_slot=40_000,
            reference_available_cash=4_000,
        )
    )

    assert codes[0] == "600010"
    assert len(codes) == 10


def test_v4_opinion_targets_follow_each_new_orders_remaining_cash() -> None:
    first = _candidate(
        code="600000",
        base_grade="B",
        preliminary_rank=1,
        strong_evidence_ids=["strong-candle", "strong-rsi"],
        close=10,
        stop_price=8,
        pressure_target=12,
    )
    cash_sensitive = [
        _candidate(
            code=f"{600001 + index:06d}",
            base_grade="B",
            preliminary_rank=index + 2,
            close=10,
            stop_price=9.95,
            pressure_target=10.14,
        )
        for index in range(9)
    ]
    later_priority = _candidate(
        code="600010",
        base_grade="B",
        preliminary_rank=11,
        net_reward_risk_ratio=None,
        fundamental_priority="preferred",
        close=10,
        stop_price=8,
        pressure_target=12,
    )

    codes = rank_candidates_for_opinion(
        OpinionTargetSelectionInput(
            strategy_version=CORE_STRATEGY_VERSION,
            market_state="rising",
            candidates=[first, *cash_sensitive, later_priority],
            reference_full_position_slot=100_000 / 3,
            reference_available_cash=40_000,
        )
    )

    assert codes[:2] == ["600000", "600010"]
    assert len(codes) == 10
