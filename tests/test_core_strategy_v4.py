from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import fourseasquant.core_strategy_orchestrator as orchestrator
from fourseasquant.core_strategy_adapters import CoreStrategyDailyInputs
from fourseasquant.core_strategy_execution import (
    EntryCandidate,
    PortfolioForEntry,
)
from fourseasquant.core_strategy_orchestrator import (
    prepare_strategy_investigations,
)
from fourseasquant.core_strategy_positions import (
    HoldingPosition,
    PositionObservation,
)
from fourseasquant.core_strategy_repository import CoreStrategyInputVersions


ACTUAL_DATE = date(2026, 7, 29)
BEIJING = ZoneInfo("Asia/Shanghai")


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
            market_environment="market-v1",
            technical_scores="technical-v3:test-qfq",
            industry_chain="industry-chain:empty",
        ),
        candidates=[],
        reasons=[],
    )


def _candidate(
    code: str,
    *,
    rank: int,
    stop_price: float,
    pressure_target: float,
) -> EntryCandidate:
    return EntryCandidate(
        code=code,
        name=f"股票{code}",
        route="technical_mainline",
        base_grade="B",
        preliminary_rank=rank,
        entry_triggers=["support_retest"],
        close=10,
        stop_price=stop_price,
        pressure_target=pressure_target,
    )


def test_v4_opinion_ranking_forecasts_same_day_exit_cash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "strategy-v4.db"
    cash_sensitive = [
        _candidate(
            f"{600000 + index:06d}",
            rank=index + 1,
            stop_price=9.56,
            pressure_target=11,
        )
        for index in range(10)
    ]
    cash_beneficiary = _candidate(
        "600010",
        rank=11,
        stop_price=9.94,
        pressure_target=10.23,
    ).model_copy(
        update={"fundamental_priority": "preferred"}
    )
    monkeypatch.setattr(
        orchestrator,
        "load_core_strategy_daily_inputs",
        lambda *_, **__: _daily_inputs(),
    )
    monkeypatch.setattr(
        orchestrator,
        "_entry_candidates",
        lambda *_, **__: [*cash_sensitive, cash_beneficiary],
    )
    portfolio = PortfolioForEntry(
        initial_capital=40_000,
        net_asset_value=40_000,
        available_cash=4_000,
        held_codes=["600099"],
    )
    position = HoldingPosition(
        code="600099",
        name="待退出持仓",
        shares=3_600,
        cost_price=10,
        stop_price=9.5,
        pressure_target=12,
        initial_risk=0.5,
        highest_close_since_entry=10,
        pending_exit_reason="stop_close_break",
    )
    observation = PositionObservation(
        code="600099",
        open=10,
        high=10,
        low=10,
        close=10,
        limit_down_price=9,
    )

    preparation = prepare_strategy_investigations(
        database,
        requested_date=ACTUAL_DATE,
        strategy_version="core-strategy-v4",
        corporate_actions_complete=True,
        portfolio=portfolio,
        positions=[position],
        observations=[observation],
        fundamental_investigations=None,
        published_at=datetime(2026, 7, 29, 16, 30, tzinfo=BEIJING),
    )

    assert preparation.opinion_targets.codes[0] == "600010"
    assert len(preparation.opinion_targets.codes) == 10
