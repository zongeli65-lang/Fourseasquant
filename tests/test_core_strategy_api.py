from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from fourseasquant.core_strategy import CandidatePipelineDecision
from fourseasquant.core_strategy_execution import EntryPlanningDecision
from fourseasquant.core_strategy_positions import PositionManagementDecision
from fourseasquant.core_strategy_repository import (
    CoreStrategyDaySnapshot,
    CoreStrategyInputVersions,
    CoreStrategyPortfolioSnapshot,
    publish_core_strategy_day,
    publish_core_strategy_portfolio,
)
from fourseasquant.main import app


ACTUAL_DATE = date(2026, 7, 29)
VERSION = "core-strategy-v1-test"
BEIJING = ZoneInfo("Asia/Shanghai")


def _day_snapshot() -> CoreStrategyDaySnapshot:
    return CoreStrategyDaySnapshot(
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        market_state="sideways",
        input_versions=CoreStrategyInputVersions(
            market_environment="market-v1",
            technical_scores="technical-v3",
        ),
        candidate_pipeline=CandidatePipelineDecision(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            candidates=[],
            fundamental_targets=[],
        ),
        entry_planning=EntryPlanningDecision(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            maximum_positions=1,
            full_position_slot=100_000,
            candidates=[],
            orders=[],
            remaining_cash=100_000,
        ),
        position_management=PositionManagementDecision(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            publication_blocked=False,
            decisions=[],
            positions=[],
            orders=[],
        ),
    )


def _portfolio_snapshot() -> CoreStrategyPortfolioSnapshot:
    return CoreStrategyPortfolioSnapshot(
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        initial_capital=100_000,
        available_cash=100_000,
        net_asset_value=100_000,
        positions=[],
        marks=[],
    )


def test_core_strategy_api_reads_exact_and_latest_snapshots(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    published_at = datetime(2026, 7, 29, 16, tzinfo=BEIJING)

    with TestClient(app) as client:
        missing = client.get(
            "/api/core-strategy/days/latest",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )
        publish_core_strategy_day(
            database,
            snapshot=_day_snapshot(),
            published_at=published_at,
        )
        publish_core_strategy_portfolio(
            database,
            snapshot=_portfolio_snapshot(),
            published_at=published_at,
        )
        exact_day = client.get(
            f"/api/core-strategy/days/{ACTUAL_DATE.isoformat()}",
            params={"strategy_version": VERSION},
        )
        latest_day = client.get(
            "/api/core-strategy/days/latest",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )
        exact_portfolio = client.get(
            f"/api/core-strategy/portfolio/{ACTUAL_DATE.isoformat()}",
            params={"strategy_version": VERSION},
        )
        latest_portfolio = client.get(
            "/api/core-strategy/portfolio/latest",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )

    assert missing.status_code == 404
    assert exact_day.status_code == 200
    assert latest_day.status_code == 200
    assert exact_day.json()["snapshot"]["market_state"] == "sideways"
    assert exact_portfolio.status_code == 200
    assert latest_portfolio.status_code == 200
    assert exact_portfolio.json()["snapshot"]["net_asset_value"] == 100_000


def test_core_strategy_api_rejects_blank_strategy_version(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-api-invalid.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        response = client.get(
            f"/api/core-strategy/days/{ACTUAL_DATE.isoformat()}",
            params={"strategy_version": " "},
        )

    assert response.status_code == 422
    assert "策略版本不能为空" in response.text


def test_core_strategy_api_initializes_account_without_inventing_capital(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-account-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        initial = client.get(
            "/api/core-strategy/run-status",
            params={"target_date": ACTUAL_DATE.isoformat()},
        )
        initialized = client.post(
            "/api/core-strategy/account",
            json={"initial_capital": 80_000},
        )
        ready = client.get(
            "/api/core-strategy/run-status",
            params={"target_date": ACTUAL_DATE.isoformat()},
        )
        conflicting = client.post(
            "/api/core-strategy/account",
            json={"initial_capital": 100_000},
        )
        rejected_reset = client.request(
            "DELETE",
            "/api/core-strategy/account",
            json={"confirmation": "取消"},
        )
        reset = client.request(
            "DELETE",
            "/api/core-strategy/account",
            json={"confirmation": "清空全部"},
        )
        after_reset = client.get(
            "/api/core-strategy/run-status",
            params={"target_date": ACTUAL_DATE.isoformat()},
        )

    assert initial.status_code == 200
    assert initial.json()["stage"] == "account_uninitialized"
    assert initialized.status_code == 201
    assert initialized.json()["initial_capital"] == 80_000
    assert ready.status_code == 200
    assert ready.json()["stage"] == "not_prepared"
    assert conflicting.status_code == 409
    assert rejected_reset.status_code == 422
    assert reset.status_code == 200
    assert reset.json()["previous_initial_capital"] == 80_000
    assert after_reset.json()["stage"] == "account_uninitialized"
