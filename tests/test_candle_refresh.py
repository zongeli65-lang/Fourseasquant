from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pytest import MonkeyPatch

import fourseasquant.candle_refresh as candle_refresh_module
from fourseasquant.akshare_history import HistoryImportSummary
from fourseasquant.candle_refresh import CandleRefreshError, refresh_one_year_candles
from fourseasquant.database import initialize_database


class _CompletedImporter:
    def __init__(self, range_end: date) -> None:
        self._range_end = range_end

    def import_one_year(self, **_: object) -> HistoryImportSummary:
        return HistoryImportSummary(
            range_start=date(2025, 7, 24),
            range_end=self._range_end,
            total_symbols=1,
            completed_symbols=1,
            failed_codes=[],
            published_days=1,
        )


def test_refresh_rejects_previous_trading_day_as_target_day_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "candles.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-23', 'akshare_sina_daily_qfq:2026-07-24', 'now')
            """
        )

    previous_day_importer = _CompletedImporter(date(2026, 7, 23))
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: previous_day_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        lambda **_: previous_day_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        lambda *_, **__: {},
    )

    with pytest.raises(CandleRefreshError, match="目标交易日"):
        refresh_one_year_candles(
            database,
            requested_end_date=date(2026, 7, 24),
        )


def test_refresh_rejects_stale_qfq_data_when_raw_data_reaches_target(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "qfq-candles.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-23', 'akshare_sina_daily_qfq:2026-07-24', 'now')
            """
        )

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 24)),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 23)),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        lambda *_, **__: {},
    )

    with pytest.raises(CandleRefreshError, match="前复权股票日线仅到 2026-07-23"):
        refresh_one_year_candles(
            database,
            requested_end_date=date(2026, 7, 24),
        )


def test_refresh_rejects_stock_missing_from_both_target_day_sources(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "missing-active-stock.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', 'akshare_sina_daily_qfq:2026-07-24', 'old')
            """
        )
        for source in (
            "akshare_sina_daily",
            "akshare_sina_daily_qfq:2026-07-24",
        ):
            for trading_date, codes in (
                ("2026-07-23", ("600000", "000001")),
                ("2026-07-24", ("600000",)),
            ):
                connection.executemany(
                    """
                    INSERT INTO historical_security_facts (
                        source, actual_data_date, code, name, open, high, low,
                        close, previous_close, change_pct, volume, turnover_cny,
                        listing_trading_days
                    ) VALUES (?, ?, ?, '测试股票', 10, 11, 9, 10, 10, 0, 100, 1000, 100)
                    """,
                    [(source, trading_date, code) for code in codes],
                )
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO history_ingestion_progress (
                        source, range_start, range_end, code, row_count, completed_at
                    ) VALUES (?, '2025-07-24', '2026-07-24', ?, 2, 'now')
                    """,
                    [(source, code) for code in codes],
                )

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 24)),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 24)),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        lambda *_, **__: {},
    )

    with pytest.raises(CandleRefreshError, match="000001"):
        refresh_one_year_candles(
            database,
            requested_end_date=date(2026, 7, 24),
            security_daily_fact_snapshot=lambda: pd.DataFrame(
                [
                    {"代码": "600000", "名称": "浦发银行", "成交量": 100},
                    {"代码": "000001", "名称": "平安银行", "成交量": 100},
                ]
            ),
        )
    with sqlite3.connect(database) as connection:
        stale_progress = connection.execute(
            """
            SELECT source FROM history_ingestion_progress
            WHERE range_start = '2025-07-24'
              AND range_end = '2026-07-24'
              AND code = '000001'
            """
        ).fetchall()
        stale_publication = connection.execute(
            """
            SELECT 1 FROM candle_dataset_publications
            WHERE actual_data_date = '2026-07-24'
            """
        ).fetchone()
    assert stale_progress == []
    assert stale_publication is None


def test_historical_refresh_requires_target_day_coverage_evidence(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "historical-coverage.db"
    initialize_database(database)
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 23)),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        lambda **_: _CompletedImporter(date(2026, 7, 23)),
    )

    with pytest.raises(CandleRefreshError, match="历史目标日缺少个股日频事实覆盖证据"):
        refresh_one_year_candles(
            database,
            requested_end_date=date(2026, 7, 23),
        )
