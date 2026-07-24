from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from fourseasquant.akshare_market_data import (
    BenchmarkDailyFact,
    DailyMarketFacts,
    SecurityDailyFact,
)
from fourseasquant.database import (
    claim_automation_date,
    create_task_run,
    initialize_database,
    release_automation_date,
)
from fourseasquant.main import app


def test_market_data_collection_endpoint_returns_akshare_batch_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    facts = DailyMarketFacts(
        source="akshare",
        requested_date=date(2026, 7, 22),
        actual_data_date=date(2026, 7, 21),
        benchmark=BenchmarkDailyFact(
            name="沪深 300",
            date=date(2026, 7, 21),
            open=4700,
            high=4750,
            low=4680,
            close=4739.229,
            volume=30_000_000_000,
        ),
        securities=[
            SecurityDailyFact(
                code="000001",
                name="平安银行",
                date=date(2026, 7, 21),
                open=12.1,
                high=12.2,
                low=11.9,
                close=12.0,
                previous_close=12.06,
                change_pct=-0.5,
                volume=2_000_000,
                turnover_cny=24_000_000,
                listing_trading_days=8_000,
            )
        ],
    )
    monkeypatch.setattr(
        "fourseasquant.main.collect_and_store_daily_facts",
        lambda *args, **kwargs: facts,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/market-data/daily",
            json={"target_date": "2026-07-22"},
        )

    assert response.status_code == 201
    assert response.json() == {
        "source": "akshare",
        "requested_date": "2026-07-22",
        "actual_data_date": "2026-07-21",
        "benchmark": "沪深 300",
        "benchmark_close": 4739.229,
        "security_count": 1,
    }


def test_candle_status_reports_update_in_progress_and_last_complete_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "candle-status.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-23', 'akshare_sina_daily_qfq:2026-07-23', 'now')
            """
        )
    started_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    create_task_run(
        database,
        date(2026, 7, 24),
        started_at,
        trigger_method="retry",
    )
    claim_id = claim_automation_date(database, date(2026, 7, 24), started_at)
    assert claim_id is not None
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/market-data/candles/status",
                params={"target_date": "2026-07-24"},
            )
    finally:
        release_automation_date(database, date(2026, 7, 24), claim_id)

    assert response.status_code == 200
    assert response.json() == {
        "requested_date": "2026-07-24",
        "latest_published_date": "2026-07-23",
        "status": "updating",
        "message": "K线正在更新：目标日期 2026-07-24，当前完整版本 2026-07-23。",
    }


def test_candle_status_does_not_treat_orphaned_running_task_as_updating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "orphaned-task.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-23', 'akshare_sina_daily_qfq:2026-07-23', 'now')
            """
        )
    create_task_run(
        database,
        date(2026, 7, 24),
        datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(hours=3),
        trigger_method="retry",
    )
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        response = client.get(
            "/api/market-data/candles/status",
            params={"target_date": "2026-07-24"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "stale"
