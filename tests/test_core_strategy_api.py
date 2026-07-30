from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
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


def _day_snapshot(
    *,
    actual_date: date = ACTUAL_DATE,
    version: str = VERSION,
) -> CoreStrategyDaySnapshot:
    return CoreStrategyDaySnapshot(
        actual_date=actual_date,
        strategy_version=version,
        market_state="sideways",
        input_versions=CoreStrategyInputVersions(
            market_environment="market-v1",
            technical_scores="technical-v3",
        ),
        candidate_pipeline=CandidatePipelineDecision(
            actual_date=actual_date,
            strategy_version=version,
            candidates=[],
            fundamental_targets=[],
        ),
        entry_planning=EntryPlanningDecision(
            actual_date=actual_date,
            strategy_version=version,
            maximum_positions=1,
            full_position_slot=100_000,
            candidates=[],
            orders=[],
            remaining_cash=100_000,
        ),
        position_management=PositionManagementDecision(
            actual_date=actual_date,
            strategy_version=version,
            publication_blocked=False,
            decisions=[],
            positions=[],
            orders=[],
        ),
    )


def _portfolio_snapshot(
    *,
    actual_date: date = ACTUAL_DATE,
    version: str = VERSION,
    initial_capital: float = 100_000,
    available_cash: float = 100_000,
    net_asset_value: float = 100_000,
) -> CoreStrategyPortfolioSnapshot:
    return CoreStrategyPortfolioSnapshot(
        actual_date=actual_date,
        strategy_version=version,
        initial_capital=initial_capital,
        available_cash=available_cash,
        net_asset_value=net_asset_value,
        positions=[],
        marks=[],
    )


def test_core_strategy_performance_uses_continuous_real_portfolio_nav(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-performance-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    first_date = date(2026, 7, 28)
    observations = [
        (first_date, 110_000.0),
        (ACTUAL_DATE, 99_000.0),
    ]
    for actual_date, net_asset_value in observations:
        published_at = datetime(
            actual_date.year,
            actual_date.month,
            actual_date.day,
            19,
            tzinfo=BEIJING,
        )
        publish_core_strategy_day(
            database,
            snapshot=_day_snapshot(actual_date=actual_date),
            published_at=published_at,
        )
        publish_core_strategy_portfolio(
            database,
            snapshot=_portfolio_snapshot(
                actual_date=actual_date,
                available_cash=net_asset_value,
                net_asset_value=net_asset_value,
            ),
            published_at=published_at,
        )

    with TestClient(app) as client:
        response = client.get(
            "/api/core-strategy/performance",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_as_of_date"] == ACTUAL_DATE.isoformat()
    assert payload["actual_date"] == ACTUAL_DATE.isoformat()
    assert payload["strategy_version"] == VERSION
    assert payload["sample_count"] == 2
    assert payload["initial_capital"] == 100_000
    assert payload["available_cash"] == 99_000
    assert payload["holding_market_value"] == 0
    assert payload["net_asset_value"] == 99_000
    assert payload["daily_profit_loss"] == -11_000
    assert payload["daily_return_pct"] == pytest.approx(-10)
    assert payload["cumulative_profit_loss"] == -1_000
    assert payload["cumulative_return_pct"] == pytest.approx(-1)
    assert payload["statistics"]["annualized_return_pct"] == pytest.approx(
        -71.8139
    )
    assert payload["statistics"]["sharpe_ratio"] == pytest.approx(0)
    assert payload["statistics"]["win_rate_pct"] == pytest.approx(50)
    assert payload["statistics"]["max_drawdown_pct"] == pytest.approx(-10)
    assert payload["statistics"]["current_drawdown_pct"] == pytest.approx(-10)
    assert payload["points"] == [
        {
            "actual_date": first_date.isoformat(),
            "net_asset_value": 110_000,
            "normalized_nav": 1.1,
            "daily_profit_loss": 10_000,
            "daily_return_pct": 10,
            "cumulative_profit_loss": 10_000,
            "cumulative_return_pct": 10,
            "drawdown_pct": 0,
        },
        {
            "actual_date": ACTUAL_DATE.isoformat(),
            "net_asset_value": 99_000,
            "normalized_nav": 0.99,
            "daily_profit_loss": -11_000,
            "daily_return_pct": -10,
            "cumulative_profit_loss": -1_000,
            "cumulative_return_pct": -1,
            "drawdown_pct": -10,
        },
    ]


def test_core_strategy_performance_explains_unavailable_statistics(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-performance-short-history.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    published_at = datetime(2026, 7, 29, 19, tzinfo=BEIJING)
    publish_core_strategy_day(
        database,
        snapshot=_day_snapshot(),
        published_at=published_at,
    )
    publish_core_strategy_portfolio(
        database,
        snapshot=_portfolio_snapshot(
            available_cash=98_000,
            net_asset_value=98_000,
        ),
        published_at=published_at,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/core-strategy/performance",
            params={"as_of_date": date(2026, 7, 30).isoformat()},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["actual_date"] == ACTUAL_DATE.isoformat()
    assert payload["sample_count"] == 1
    assert payload["daily_profit_loss"] == -2_000
    assert payload["daily_return_pct"] == pytest.approx(-2)
    assert payload["cumulative_profit_loss"] == -2_000
    assert payload["cumulative_return_pct"] == pytest.approx(-2)
    assert payload["statistics"] == {
        "annualized_return_pct": None,
        "annualized_return_unavailable_reason": (
            "至少需要 2 个真实日频组合快照才能计算年化收益率"
        ),
        "max_drawdown_pct": -2,
        "current_drawdown_pct": -2,
        "sharpe_ratio": None,
        "sharpe_ratio_unavailable_reason": (
            "至少需要 2 个真实日频组合快照才能计算夏普比率"
        ),
        "win_rate_pct": None,
        "win_rate_unavailable_reason": (
            "至少需要 2 个真实日频组合快照才能计算胜率"
        ),
    }


def test_core_strategy_performance_returns_404_without_real_portfolio(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-performance-missing.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        response = client.get(
            "/api/core-strategy/performance",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "没有可用的真实组合绩效"


def test_core_strategy_performance_explains_zero_volatility_sharpe(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "core-strategy-performance-zero-volatility.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    for actual_date in (date(2026, 7, 28), ACTUAL_DATE):
        published_at = datetime(
            actual_date.year,
            actual_date.month,
            actual_date.day,
            19,
            tzinfo=BEIJING,
        )
        publish_core_strategy_day(
            database,
            snapshot=_day_snapshot(actual_date=actual_date),
            published_at=published_at,
        )
        publish_core_strategy_portfolio(
            database,
            snapshot=_portfolio_snapshot(actual_date=actual_date),
            published_at=published_at,
        )

    with TestClient(app) as client:
        response = client.get(
            "/api/core-strategy/performance",
            params={"as_of_date": ACTUAL_DATE.isoformat()},
        )

    assert response.status_code == 200
    statistics = response.json()["statistics"]
    assert statistics["annualized_return_pct"] == 0
    assert statistics["win_rate_pct"] == 0
    assert statistics["sharpe_ratio"] is None
    assert (
        statistics["sharpe_ratio_unavailable_reason"]
        == "日收益率没有变化，无法计算夏普比率"
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
