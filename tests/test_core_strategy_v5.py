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
from fourseasquant.core_strategy_orchestrator import (
    finalize_strategy_day,
    prepare_strategy_investigations,
)
from fourseasquant.core_strategy_repository import (
    CoreStrategyInputVersions,
    read_core_strategy_day,
    read_core_strategy_portfolio,
)
from fourseasquant.core_strategy_technical import (
    DailyTechnicalAnalysis,
    TechnicalLevel,
)


ACTUAL_DATE = date(2026, 7, 29)
BEIJING = ZoneInfo("Asia/Shanghai")
VERSION = "core-strategy-v5-test"


def _prepared_candidate(
    code: str,
    *,
    rank: int,
    previous_close: float,
    close: float,
) -> PreparedStrategyCandidate:
    technical = DailyTechnicalAnalysis(
        code=code,
        name=f"股票{code}",
        actual_date=ACTUAL_DATE,
        qfq_source="test-qfq",
        status="ready",
        entry_triggers=["support_retest"],
        strong_evidence_ids=[
            f"candlestick:bullish_engulfing:{ACTUAL_DATE.isoformat()}"
        ],
        supports=[
            TechnicalLevel(
                role="support",
                lower=close * 0.89,
                center=close * 0.9,
                upper=close * 0.91,
                formed_on=date(2026, 7, 20),
                sources=["swing_low"],
                source_count=1,
            )
        ],
        stop_price=close * 0.88,
        pressure_target=close * 1.2,
        gross_reward_risk_ratio=1.67,
    )
    return PreparedStrategyCandidate(
        preliminary=PreliminaryCandidate(
            code=code,
            name=f"股票{code}",
            route="technical_mainline",
            base_grade="B",
            preliminary_rank=rank,
            technical_trade_permission=True,
        ),
        technical=technical,
        technical_rankings=[],
        industry_selection_refs=[],
        raw_bar=RawDailyBar(
            open=close,
            high=close,
            low=close,
            close=close,
            previous_close=previous_close,
            change_pct=(close / previous_close - 1) * 100,
            volume=100_000,
            turnover_cny=100_000_000,
            listing_trading_days=300,
        ),
    )


def _daily_inputs() -> CoreStrategyDailyInputs:
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
            _prepared_candidate(
                "600001",
                rank=1,
                previous_close=3.67,
                close=4.04,
            ),
            _prepared_candidate(
                "600002",
                rank=2,
                previous_close=3.67,
                close=4.03,
            ),
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


def test_v5_raw_limit_up_is_excluded_before_immutable_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "strategy-v5.db"
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: _daily_inputs(),
    )
    published_at = datetime(2026, 7, 29, 16, 30, tzinfo=BEIJING)

    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version=VERSION,
        corporate_actions_complete=True,
        portfolio=_portfolio(),
        fundamental_investigations=None,
        published_at=published_at,
    )

    assert preparation.opinion_targets.codes == ["600002"]

    result = finalize_strategy_day(
        database,
        preparation=preparation,
        opinions=[
            StrategyOpinionEvidence(
                code="600001",
                targeted=False,
                status="not_targeted",
            ),
            StrategyOpinionEvidence(
                code="600002",
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

    decisions = {
        item.code: item
        for item in result.published.snapshot.entry_planning.candidates
    }
    assert "limit_up" in decisions["600001"].block_reasons
    assert not decisions["600001"].open_allowed
    order_codes = [
        order.code
        for order in result.published.snapshot.entry_planning.orders
    ]
    assert order_codes == ["600002"]
    assert read_core_strategy_day(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
    ) is not None
    assert read_core_strategy_portfolio(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
    ) is not None
