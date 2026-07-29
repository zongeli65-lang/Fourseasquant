from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain import (
    LocalIndustryChainData,
    StockScopeViolation,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def _prepared(tmp_path: Path) -> tuple[Path, LocalIndustryChainData, str]:
    database = tmp_path / "company-evidence.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        for code, name in (("000001", "主板公司甲"), ("600001", "主板公司乙")):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low, close,
                    previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (
                    'akshare_sina_daily', '2026-07-24', ?, ?, 9.8, 10.2, 9.7,
                    10.0, 9.9, 1.0, 1000, 10000, 100
                )
                """,
                (code, name),
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
        connection.execute(
            """
            INSERT INTO personal_fundamental_monthly_snapshots (
                code, as_of_date, rules_version, changed_fields_json,
                source_urls_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "000001",
                "2026-07-24",
                "personal-fundamental-v1",
                json.dumps(
                    {
                        "main_business_name": "示例材料",
                        "main_business_profit_share": 0.45,
                    }
                ),
                '["https://example.test/business"]',
                "2026-07-25T10:50:00+08:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO personal_fundamental_monthly_batches (
                as_of_date, rules_version, expected_codes_json,
                completed_codes_json, status, created_at, published_at
            ) VALUES (?, ?, ?, ?, 'published', ?, ?)
            """,
            (
                "2026-07-24",
                "personal-fundamental-v1",
                '["000001"]',
                '["000001"]',
                "2026-07-25T11:00:00+08:00",
                "2026-07-25T11:00:00+08:00",
            ),
        )
    local_data = LocalIndustryChainData(database)
    universe = local_data.eligible_universe(
        as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING)
    )
    return database, local_data, universe.universe_version


def test_company_evidence_returns_available_and_explicitly_missing_rows(
    tmp_path: Path,
) -> None:
    _, local_data, universe_version = _prepared(tmp_path)

    results = local_data.company_evidence(
        universe_version=universe_version,
        codes=["000001", "600001"],
        as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING),
    )

    assert results[0].availability == "available"
    assert results[0].main_business_name == "示例材料"
    assert results[0].gross_profit_share == 0.45
    assert results[0].revenue_share is None
    assert results[0].ownership_evidence_available is False
    assert results[1].availability == "unavailable"


def test_company_query_rejects_code_outside_frozen_universe(
    tmp_path: Path,
) -> None:
    _, local_data, universe_version = _prepared(tmp_path)

    with pytest.raises(StockScopeViolation, match="不在合格范围"):
        local_data.company_evidence(
            universe_version=universe_version,
            codes=["300001"],
            as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING),
        )


def test_company_evidence_published_in_future_is_not_visible(
    tmp_path: Path,
) -> None:
    _, local_data, universe_version = _prepared(tmp_path)

    result = local_data.company_evidence(
        universe_version=universe_version,
        codes=["000001"],
        as_of_time=datetime(2026, 7, 25, 10, 59, tzinfo=BEIJING),
    )[0]

    assert result.availability == "unavailable"
    assert "没有已发布" in result.limitations[0]


def test_company_adapter_never_reads_technical_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, local_data, universe_version = _prepared(tmp_path)
    read_tables: set[str] = set()
    real_connect = sqlite3.connect

    def traced_connect(path: str | Path) -> sqlite3.Connection:
        connection = real_connect(path)

        def authorize(
            action: int,
            table: str | None,
            _column: str | None,
            _database: str | None,
            _trigger: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_READ and table is not None:
                read_tables.add(table)
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    local_data.company_evidence(
        universe_version=universe_version,
        codes=["000001"],
        as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING),
    )

    assert not any(table.startswith("technical_") for table in read_tables)


def test_company_name_mention_without_business_match_is_not_recalled(
    tmp_path: Path,
) -> None:
    _, local_data, universe_version = _prepared(tmp_path)

    results = local_data.recall_company_evidence(
        universe_version=universe_version,
        terms=["储能电芯", "锂离子电池"],
        corpus=["新闻正文提到了主板公司甲，但没有主营业务关系。"],
        as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING),
        limit=8,
    )

    assert results == ()


def test_controlled_industry_alias_can_recall_main_business(
    tmp_path: Path,
) -> None:
    database, local_data, universe_version = _prepared(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE personal_fundamental_monthly_snapshots
            SET changed_fields_json = json_set(
                changed_fields_json,
                '$.main_business_name',
                '玻璃产品'
            )
            WHERE code = '000001'
            """
        )

    results = local_data.recall_company_evidence(
        universe_version=universe_version,
        terms=["光伏玻璃"],
        corpus=[],
        as_of_time=datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING),
        limit=8,
    )

    assert [item.code for item in results] == ["000001"]
