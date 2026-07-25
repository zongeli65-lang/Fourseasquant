from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    LynchFinancialBase,
)
from fourseasquant.fundamental_lynch_repository import (
    read_latest_published_lynch_daily_batch,
    save_lynch_financial_base_batch,
)
from fourseasquant.fundamental_lynch_service import (
    FinancialBaseExpired,
    read_lynch_market_universe,
    run_lynch_daily_calculation,
)


def _insert_fact(
    path: Path,
    *,
    code: str,
    name: str,
    close: float = 10,
    listing_days: int = 120,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "akshare_sina_daily",
                "2026-07-24",
                code,
                name,
                close,
                close,
                close,
                close,
                close,
                0,
                1,
                1,
                listing_days,
            ),
        )


def _base(code: str, name: str) -> LynchFinancialBase:
    return LynchFinancialBase(
        code=code,
        name=name,
        financial_as_of=date(2026, 3, 31),
        latest_notice_date=date(2026, 4, 25),
        annual_adjusted_eps=[
            AnnualAdjustedEps(year=2023, value=1),
            AnnualAdjustedEps(year=2024, value=1.2),
            AnnualAdjustedEps(year=2025, value=1.44),
        ],
        ttm_adjusted_eps=1.5,
        prior_ttm_adjusted_eps=1.3,
        ttm_dividend_per_share=0.2,
        audit_status="standard_unqualified",
    )


def test_universe_includes_main_chinext_star_and_excludes_invalid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite3"
    initialize_database(path)
    for code, name, listing_days in (
        ("000001", "平安银行", 120),
        ("300001", "特锐德", 120),
        ("301001", "凯淳股份", 120),
        ("600000", "浦发银行", 120),
        ("688001", "华兴源创", 120),
        ("920001", "北交样本", 120),
        ("000002", "ST样本", 120),
        ("000003", "退市样本", 120),
        ("001001", "新股样本", 59),
    ):
        _insert_fact(path, code=code, name=name, listing_days=listing_days)

    universe = read_lynch_market_universe(path, date(2026, 7, 24))

    assert [item.code for item in universe.securities] == [
        "000001",
        "300001",
        "301001",
        "600000",
        "688001",
    ]


def test_daily_calculation_keeps_missing_financial_as_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite3"
    initialize_database(path)
    _insert_fact(path, code="600000", name="浦发银行")
    _insert_fact(path, code="688001", name="华兴源创")
    save_lynch_financial_base_batch(
        path,
        target_date=date(2026, 7, 1),
        expected_codes=["600000"],
        bases=[_base("600000", "浦发银行")],
        errors={},
        collected_at=datetime(2026, 7, 1, 18),
    )

    outcome = run_lynch_daily_calculation(path, date(2026, 7, 24))

    assert outcome.published is True
    publication = read_latest_published_lynch_daily_batch(
        path, date(2026, 7, 24)
    )
    assert publication is not None
    assert publication.results["600000"].calculable is True
    assert publication.results["688001"].unavailable_reason == (
        "financial_data_unavailable"
    )


def test_daily_calculation_rejects_base_older_than_45_days(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.sqlite3"
    initialize_database(path)
    _insert_fact(path, code="600000", name="浦发银行")
    old_date = date(2026, 7, 24) - timedelta(days=46)
    save_lynch_financial_base_batch(
        path,
        target_date=old_date,
        expected_codes=["600000"],
        bases=[_base("600000", "浦发银行")],
        errors={},
        collected_at=datetime(2026, 6, 1, 18),
    )

    with pytest.raises(FinancialBaseExpired):
        run_lynch_daily_calculation(path, date(2026, 7, 24))
