from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from fourseasquant.database import initialize_database
from fourseasquant.main import app
from fourseasquant.market_environment import (
    RULES_VERSION,
    read_market_environment,
    read_market_environment_history,
    refresh_market_environment,
)


BEIJING = ZoneInfo("Asia/Shanghai")
START = date(2026, 1, 1)


def _seed_market(database: Path, *, crash_last_day: bool = False) -> date:
    index_rows: list[tuple[object, ...]] = []
    security_rows: list[tuple[object, ...]] = []
    prices = {"sh000001": 3000.0, "sz399001": 10000.0}
    stock_prices = {
        "600001": 10.0,
        "601001": 12.0,
        "000001": 8.0,
        "002001": 15.0,
    }
    names = {
        "600001": "沪市一号",
        "601001": "沪市二号",
        "000001": "深市一号",
        "002001": "深市二号",
    }
    for offset in range(45):
        trading_date = START + timedelta(days=offset)
        final_crash = crash_last_day and offset == 44
        for code, name in (("sh000001", "上证指数"), ("sz399001", "深证成指")):
            previous = prices[code]
            growth = (
                -0.10
                if final_crash
                else 0.01
                if offset >= 20
                else 0.0005
            )
            close = previous * (1 + growth)
            prices[code] = close
            index_rows.append(
                (
                    f"akshare_index_{code}",
                    trading_date.isoformat(),
                    name,
                    previous,
                    max(previous, close) * 1.002,
                    min(previous, close) * 0.998,
                    close,
                    1_000_000 + offset * 10_000,
                )
            )
        for position, (code, previous) in enumerate(list(stock_prices.items())):
            if final_crash:
                close = round(previous * 0.90, 2)
            else:
                change = 0.012 if position < 3 else -0.003
                close = previous * (1 + change)
            stock_prices[code] = close
            change_pct = (close / previous - 1) * 100
            security_rows.append(
                (
                    "akshare_sina_daily",
                    trading_date.isoformat(),
                    code,
                    names[code],
                    previous,
                    max(previous, close),
                    min(previous, close),
                    close,
                    previous,
                    change_pct,
                    100_000,
                    3_000_000_000 if final_crash else 1_000_000_000,
                    300,
                )
            )
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            index_rows,
        )
        connection.executemany(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            security_rows,
        )
    return START + timedelta(days=44)


def test_refresh_publishes_versioned_history_without_technical_scores(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market-environment.db"
    initialize_database(database)
    target_date = _seed_market(database)

    first = refresh_market_environment(
        database,
        requested_date=target_date,
        now=datetime(2026, 2, 15, 17, 0, tzinfo=BEIJING),
    )
    second = refresh_market_environment(
        database,
        requested_date=target_date,
        now=datetime(2026, 2, 15, 17, 5, tzinfo=BEIJING),
    )
    snapshot = read_market_environment(
        database,
        requested_date=target_date,
    )
    history = read_market_environment_history(
        database,
        requested_end_date=target_date,
        limit=100,
    )

    assert first.inserted_count == first.snapshot_count
    assert second.inserted_count == 0
    assert snapshot.rules_version == RULES_VERSION
    assert snapshot.trend_state == "rising"
    assert snapshot.validation_state == "validated"
    assert all(item.fast_direction == "bullish" for item in snapshot.indices)
    assert snapshot.breadth.state == "strong"
    assert history.items[-1] == snapshot
    assert all("technical" not in source for source in snapshot.data_sources)


def test_extreme_decline_can_switch_state_on_the_same_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market-environment-crash.db"
    initialize_database(database)
    target_date = _seed_market(database, crash_last_day=True)

    refresh_market_environment(database, requested_date=target_date)
    snapshot = read_market_environment(
        database,
        requested_date=target_date,
    )

    assert snapshot.trend_state == "falling"
    assert snapshot.trend_changed is True
    assert snapshot.extreme_decline is True
    assert snapshot.breadth.state == "weak"


def test_market_environment_history_is_immutable(tmp_path: Path) -> None:
    database = tmp_path / "market-environment-immutable.db"
    initialize_database(database)
    target_date = _seed_market(database)
    refresh_market_environment(database, requested_date=target_date)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="不可修改"):
            connection.execute(
                """
                UPDATE market_environment_snapshots
                SET trend_state = 'falling'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
            connection.execute("DELETE FROM market_environment_snapshots")


def test_market_environment_api_exposes_target_date_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "market-environment-api.db"
    initialize_database(database)
    target_date = _seed_market(database)
    refresh_market_environment(database, requested_date=target_date)
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))

    with TestClient(app) as client:
        snapshot_response = client.get(
            "/api/market-environment",
            params={"target_date": target_date.isoformat()},
        )
        history_response = client.get(
            "/api/market-environment/history",
            params={"end_date": target_date.isoformat(), "limit": 10},
        )
        refresh_response = client.post(
            "/api/market-environment/refresh",
            json={"target_date": target_date.isoformat()},
        )

    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["actual_data_date"] == target_date.isoformat()
    assert history_response.status_code == 200
    assert len(history_response.json()["items"]) == 10
    assert refresh_response.status_code == 201
    assert refresh_response.json()["inserted_count"] == 0
