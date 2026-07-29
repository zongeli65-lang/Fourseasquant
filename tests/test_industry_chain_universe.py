from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain import (
    EligibleUniverseUnavailable,
    LocalIndustryChainData,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "universe.db"
    initialize_database(database)
    rows = (
        ("000001", "深市主板", 10.0, 1000, 10_000, 100),
        ("600001", "沪市主板", 10.0, 1000, 10_000, 100),
        ("300001", "创业板", 10.0, 1000, 10_000, 100),
        ("688001", "科创板", 10.0, 1000, 10_000, 100),
        ("000002", "ST样本", 10.0, 1000, 10_000, 100),
        ("600002", "退市样本", 10.0, 1000, 10_000, 100),
        ("000003", "新上市", 10.0, 1000, 10_000, 59),
        ("600003", "停牌样本", 10.0, 0, 0, 100),
    )
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (
                'akshare_sina_daily', '2026-07-24', ?, ?, 9.8, 10.2, 9.7, ?,
                9.9, 1.0, ?, ?, ?
            )
            """,
            rows,
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES (
                '2026-07-24', 'akshare_sina_daily_qfq:2026-07-24',
                '2026-07-24T22:00:00+08:00'
            )
            """
        )
    return database


def test_universe_freezes_only_eligible_main_board_stocks(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    local_data = LocalIndustryChainData(database)

    universe = local_data.eligible_universe(
        as_of_time=datetime(2026, 7, 24, 23, 0, tzinfo=BEIJING)
    )

    assert [item.code for item in universe.securities] == ["000001", "600001"]
    assert universe.actual_data_date.isoformat() == "2026-07-24"
    assert universe.universe_version.startswith("main-board:2026-07-24:")
    with sqlite3.connect(database) as connection:
        frozen = connection.execute(
            """
            SELECT security_count
            FROM industry_chain_stock_universes
            WHERE universe_version = ?
            """,
            (universe.universe_version,),
        ).fetchone()
    assert frozen == (2,)


def test_same_published_universe_is_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    local_data = LocalIndustryChainData(database)
    as_of_time = datetime(2026, 7, 24, 23, 0, tzinfo=BEIJING)

    first = local_data.eligible_universe(as_of_time=as_of_time)
    second = local_data.eligible_universe(as_of_time=as_of_time)

    assert second == first
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_stock_universes"
        ).fetchone()
    assert count == (1,)


def test_future_publication_is_not_visible(tmp_path: Path) -> None:
    local_data = LocalIndustryChainData(_database(tmp_path))

    with pytest.raises(EligibleUniverseUnavailable, match="之前"):
        local_data.eligible_universe(
            as_of_time=datetime(2026, 7, 24, 21, 59, tzinfo=BEIJING)
        )
