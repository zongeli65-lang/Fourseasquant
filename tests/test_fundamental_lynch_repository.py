from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    LynchFinancialBase,
    calculate_lynch_daily_result,
    rank_lynch_results,
)
from fourseasquant.fundamental_lynch_repository import (
    read_latest_published_lynch_daily_batch,
    read_latest_published_lynch_financial_batch,
    save_lynch_daily_batch,
    save_lynch_financial_base_batch,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def financial(code: str) -> LynchFinancialBase:
    return LynchFinancialBase(
        code=code,
        name=f"股票{code}",
        financial_as_of=date(2026, 3, 31),
        latest_notice_date=date(2026, 4, 30),
        annual_adjusted_eps=[
            AnnualAdjustedEps(year=2023, value=1.0),
            AnnualAdjustedEps(year=2024, value=1.2),
            AnnualAdjustedEps(year=2025, value=1.44),
        ],
        ttm_adjusted_eps=1.5,
        prior_ttm_adjusted_eps=1.3,
        ttm_dividend_per_share=0.3,
        audit_status="standard_unqualified",
        source_urls=["https://example.test/financial"],
    )


def test_financial_batch_publishes_only_complete_expected_universe(
    tmp_path: Path,
) -> None:
    database = tmp_path / "financial-batch.db"
    initialize_database(database)
    target = date(2026, 7, 24)
    now = datetime(2026, 7, 24, 16, 31, tzinfo=BEIJING)

    failed = save_lynch_financial_base_batch(
        database,
        target_date=target,
        expected_codes=["600000", "600001"],
        bases=[financial("600000")],
        errors={"600001": "接口超时"},
        collected_at=now,
    )
    assert failed.published is False
    assert read_latest_published_lynch_financial_batch(database, target) is None

    succeeded = save_lynch_financial_base_batch(
        database,
        target_date=target,
        expected_codes=["600000", "600001"],
        bases=[financial("600000"), financial("600001")],
        errors={},
        collected_at=now,
    )
    publication = read_latest_published_lynch_financial_batch(database, target)

    assert succeeded.published is True
    assert publication is not None
    assert publication.target_date == target
    assert publication.expected_codes == ["600000", "600001"]
    assert set(publication.bases) == {"600000", "600001"}


def test_failed_daily_batch_preserves_previous_complete_publication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "daily-batch.db"
    initialize_database(database)
    previous = date(2026, 7, 23)
    target = date(2026, 7, 24)
    now = datetime(2026, 7, 24, 16, 31, tzinfo=BEIJING)
    results = rank_lynch_results(
        [
            calculate_lynch_daily_result(
                financial("600000"),
                target_date=previous,
                close=10,
            ),
            calculate_lynch_daily_result(
                financial("600001"),
                target_date=previous,
                close=12,
            ),
        ]
    )
    save_lynch_daily_batch(
        database,
        target_date=previous,
        financial_base_date=previous,
        expected_codes=["600000", "600001"],
        results=results,
        errors={},
        calculated_at=now,
    )

    failed = save_lynch_daily_batch(
        database,
        target_date=target,
        financial_base_date=previous,
        expected_codes=["600000", "600001"],
        results=[results[0].model_copy(update={"target_date": target})],
        errors={"600001": "缺少当日收盘价"},
        calculated_at=now,
    )
    latest = read_latest_published_lynch_daily_batch(database, target)

    assert failed.published is False
    assert latest is not None
    assert latest.target_date == previous
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lynch_daily_results"
        ).fetchone() == (2,)


def test_schema_nineteen_contains_lynch_publication_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "schema.db"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert version == ("32",)
    assert {
        "lynch_financial_base_batches",
        "lynch_financial_bases",
        "lynch_financial_base_publications",
        "lynch_daily_batches",
        "lynch_daily_results",
        "lynch_daily_publications",
        "lynch_financial_collection_cache",
    }.issubset(tables)
