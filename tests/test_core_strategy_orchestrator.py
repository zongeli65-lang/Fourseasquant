from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import fourseasquant.core_strategy_orchestrator as orchestrator
from fourseasquant.core_strategy import PreliminaryCandidate
from fourseasquant.core_strategy_adapters import (
    CoreStrategyDailyInputs,
    PreparedStrategyCandidate,
    RawDailyBar,
    StrategyOpinionEvidence,
)
from fourseasquant.core_strategy_execution import PortfolioForEntry
from fourseasquant.core_strategy_positions import HoldingPosition
from fourseasquant.core_strategy_repository import (
    CoreStrategyInputVersions,
    read_latest_core_strategy_portfolio,
)
from fourseasquant.core_strategy_technical import (
    DailyTechnicalAnalysis,
    TechnicalLevel,
)
from fourseasquant.core_strategy_orchestrator import (
    finalize_strategy_day,
    prepare_strategy_investigations,
)


ACTUAL_DATE = date(2026, 7, 28)
BEIJING = ZoneInfo("Asia/Shanghai")


def _daily_inputs() -> CoreStrategyDailyInputs:
    technical = DailyTechnicalAnalysis(
        code="600001",
        name="测试公司",
        actual_date=ACTUAL_DATE,
        qfq_source="test-qfq",
        status="ready",
        entry_triggers=["support_retest"],
        strong_evidence_ids=[
            "candlestick:bullish_engulfing:2026-07-28"
        ],
        supports=[
            TechnicalLevel(
                role="support",
                lower=9,
                center=9.1,
                upper=9.2,
                formed_on=date(2026, 7, 20),
                sources=["swing_low"],
                source_count=1,
            )
        ],
        stop_price=8.9,
        pressure_target=13,
        gross_reward_risk_ratio=2.7,
    )
    return CoreStrategyDailyInputs(
        requested_date=ACTUAL_DATE,
        actual_date=ACTUAL_DATE,
        market_state="rising",
        market_data_complete=True,
        corporate_actions_complete=True,
        technical_version="technical-v3",
        qfq_source="test-qfq",
        input_versions=CoreStrategyInputVersions(
            market_environment="market-v1:trend-1",
            technical_scores="technical-v3:test-qfq",
            industry_chain="industry-chain:empty",
        ),
        candidates=[
            PreparedStrategyCandidate(
                preliminary=PreliminaryCandidate(
                    code="600001",
                    name="测试公司",
                    route="technical_mainline",
                    base_grade="B",
                    preliminary_rank=1,
                    technical_trade_permission=True,
                ),
                technical=technical,
                technical_rankings=[],
                industry_selection_refs=[],
                raw_bar=RawDailyBar(
                    open=9.8,
                    high=10.2,
                    low=9.7,
                    close=10,
                    previous_close=9.8,
                    change_pct=2.04,
                    volume=100_000,
                    turnover_cny=100_000_000,
                    listing_trading_days=300,
                ),
            )
        ],
        reasons=[],
    )


def _portfolio() -> PortfolioForEntry:
    return PortfolioForEntry(
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=100_000,
        held_codes=[],
    )


def test_two_phase_orchestration_publishes_targets_then_trade_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "orchestrator.db"
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: _daily_inputs(),
    )
    published_at = datetime(2026, 7, 28, 16, tzinfo=BEIJING)

    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version="core-strategy-v1-test",
        corporate_actions_complete=True,
        portfolio=_portfolio(),
        fundamental_investigations=None,
        published_at=published_at,
    )

    assert preparation.opinion_targets.codes == ["600001"]
    assert [
        target.code
        for target in preparation.fundamental_targets.targets
    ] == ["600001"]

    result = finalize_strategy_day(
        database,
        preparation=preparation,
        opinions=[
            StrategyOpinionEvidence(
                code="600001",
                targeted=True,
                status="collecting",
                platform="sina",
                rules_version="opinion-test-v1",
            )
        ],
        portfolio=_portfolio(),
        positions=[],
        observations=[],
        published_at=published_at,
    )

    orders = result.published.snapshot.entry_planning.orders
    assert len(orders) == 1
    assert orders[0].stop_price == 8.9
    assert orders[0].pressure_target == 13
    assert result.ending_positions[0].stop_price == 8.9
    assert result.ending_cash < 100_000
    assert result.portfolio_publication_status == "published"
    assert result.portfolio is not None
    assert result.portfolio.snapshot.net_asset_value < 100_000


@pytest.mark.parametrize("market_state", ["sideways", "falling"])
def test_non_rising_market_skips_investigations_and_keeps_technical_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market_state: str,
) -> None:
    database = tmp_path / f"orchestrator-{market_state}.db"
    daily = _daily_inputs().model_copy(
        update={
            "market_state": market_state,
            "candidates": [
                _daily_inputs().candidates[0].model_copy(
                    update={
                        "preliminary": _daily_inputs()
                        .candidates[0]
                        .preliminary.model_copy(
                            update={"route": "pure_technical"}
                        )
                    }
                )
            ],
        }
    )
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: daily,
    )

    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version="core-strategy-v8-test",
        corporate_actions_complete=True,
        portfolio=_portfolio(),
        fundamental_investigations=None,
        published_at=datetime(2026, 7, 28, 16, tzinfo=BEIJING),
    )

    assert preparation.candidate_pipeline.candidates[0].route == (
        "pure_technical"
    )
    assert preparation.fundamental_targets.targets == []
    assert preparation.opinion_targets.codes == []


def test_finalize_rejects_unpublished_opinion_target_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "orchestrator.db"
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: _daily_inputs(),
    )
    published_at = datetime(2026, 7, 28, 16, tzinfo=BEIJING)
    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version="core-strategy-v1-test",
        corporate_actions_complete=True,
        portfolio=_portfolio(),
        fundamental_investigations=None,
        published_at=published_at,
    )

    with pytest.raises(ValueError, match="调查目标"):
        finalize_strategy_day(
            database,
            preparation=preparation,
            opinions=[
                StrategyOpinionEvidence(
                    code="600001",
                    targeted=False,
                    status="not_targeted",
                )
            ],
            portfolio=_portfolio(),
            positions=[],
            observations=[],
            published_at=published_at,
        )


def test_missing_holding_mark_does_not_publish_partial_portfolio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "orchestrator.db"
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: _daily_inputs(),
    )
    published_at = datetime(2026, 7, 28, 16, tzinfo=BEIJING)
    portfolio = PortfolioForEntry(
        initial_capital=100_000,
        net_asset_value=100_000,
        available_cash=90_000,
        held_codes=["600001"],
    )
    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version="core-strategy-v1-test",
        corporate_actions_complete=True,
        portfolio=portfolio,
        fundamental_investigations=None,
        published_at=published_at,
    )
    position = HoldingPosition(
        code="600001",
        name="测试公司",
        shares=1_000,
        cost_price=10,
        stop_price=8.9,
        pressure_target=13,
        initial_risk=1.1,
        highest_close_since_entry=10,
    )

    result = finalize_strategy_day(
        database,
        preparation=preparation,
        opinions=[
            StrategyOpinionEvidence(
                code="600001",
                targeted=False,
                status="not_targeted",
            )
        ],
        portfolio=portfolio,
        positions=[position],
        observations=[],
        published_at=published_at,
    )

    assert result.published.snapshot.actual_date == ACTUAL_DATE
    assert result.portfolio_publication_status == "blocked_missing_marks"
    assert result.portfolio is None
    assert result.portfolio_publication_blocked_codes == ["600001"]
    assert (
        read_latest_core_strategy_portfolio(
            database,
            as_of_date=ACTUAL_DATE,
        )
        is None
    )
