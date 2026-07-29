from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from pytest import MonkeyPatch

import fourseasquant.candle_refresh as candle_refresh_module
from fourseasquant.akshare_history import HistoryImportSummary
from fourseasquant.candle_refresh import CandleRefreshError, refresh_one_year_candles
from fourseasquant.candlesticks import (
    INDEXES,
    CandleDataNotFound,
    index_source,
    read_candle_series,
)
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


class _PartiallyWrittenFailedImporter:
    def __init__(self, range_end: date, *, source: str) -> None:
        self._range_end = range_end
        self._source = source

    def import_one_year(self, *, path: Path, **_: object) -> HistoryImportSummary:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, ?, '600000', '浦发银行', 99, 99, 99, 99, 10, 890, 100, 1000, 100)
                ON CONFLICT(source, actual_data_date, code) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    previous_close = excluded.previous_close,
                    change_pct = excluded.change_pct
                """,
                (self._source, self._range_end.isoformat()),
            )
        return HistoryImportSummary(
            range_start=date(2025, 7, 24),
            range_end=self._range_end,
            total_symbols=1,
            completed_symbols=0,
            failed_codes=["000001"],
            published_days=0,
        )


class _CompletedWritingImporter:
    def __init__(self, range_end: date, *, source: str, close: float) -> None:
        self._range_end = range_end
        self._source = source
        self._close = close

    def import_one_year(self, *, path: Path, **_: object) -> HistoryImportSummary:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, ?, '600000', '浦发银行', ?, ?, ?, ?, 10, 100, 100, 1000, 100)
                """,
                (
                    self._source,
                    self._range_end.isoformat(),
                    self._close,
                    self._close,
                    self._close,
                    self._close,
                ),
            )
        return HistoryImportSummary(
            range_start=date(2025, 7, 24),
            range_end=self._range_end,
            total_symbols=1,
            completed_symbols=1,
            failed_codes=[],
            published_days=0,
            listing_dates={"600000": date(2000, 1, 1)},
        )


class _CompletedRangeWritingImporter:
    def __init__(
        self,
        trading_dates: list[date],
        *,
        source: str,
        close: float,
    ) -> None:
        self._trading_dates = trading_dates
        self._source = source
        self._close = close

    def import_one_year(self, *, path: Path, **_: object) -> HistoryImportSummary:
        with sqlite3.connect(path) as connection:
            connection.executemany(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, ?, '600000', '浦发银行', ?, ?, ?, ?, 10, 100, 100, 1000, ?)
                """,
                [
                    (
                        self._source,
                        trading_date.isoformat(),
                        self._close,
                        self._close,
                        self._close,
                        self._close,
                        100 + index,
                    )
                    for index, trading_date in enumerate(self._trading_dates)
                ],
            )
            connection.execute(
                """
                INSERT INTO history_ingestion_progress (
                    source, range_start, range_end, code, row_count, completed_at
                ) VALUES (?, ?, ?, '600000', ?, '2026-07-24T16:30:00+08:00')
                """,
                (
                    self._source,
                    self._trading_dates[0].isoformat(),
                    self._trading_dates[-1].isoformat(),
                    len(self._trading_dates),
                ),
            )
        return HistoryImportSummary(
            range_start=self._trading_dates[0],
            range_end=self._trading_dates[-1],
            total_symbols=1,
            completed_symbols=1,
            failed_codes=[],
            published_days=0,
            listing_dates={"600000": date(2000, 1, 1)},
        )


def _assert_no_staging_attempt_rows(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        security_count = connection.execute(
            """
            SELECT COUNT(*) FROM historical_security_facts
            WHERE source LIKE '%:attempt-%'
            """
        ).fetchone()
        progress_count = connection.execute(
            """
            SELECT COUNT(*) FROM history_ingestion_progress
            WHERE source LIKE '%:attempt-%'
            """
        ).fetchone()
        index_count = connection.execute(
            """
            SELECT COUNT(*) FROM historical_benchmark_facts
            WHERE source LIKE '%:attempt-%'
            """
        ).fetchone()
    assert security_count == (0,)
    assert progress_count == (0,)
    assert index_count == (0,)


def test_failed_qfq_rerun_keeps_last_successful_candle_series(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "atomic-qfq-rerun.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    published_qfq_source = "akshare_sina_daily_qfq:2026-07-24"
    with sqlite3.connect(database) as connection:
        for source in ("akshare_sina_daily", published_qfq_source):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, '2026-07-24', '600000', '浦发银行', 10, 10, 10, 10, 10, 0, 100, 1000, 100)
                """,
                (source,),
            )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', ?, '2026-07-24T16:30:00+08:00')
            """,
            (published_qfq_source,),
        )

    before = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
    )
    assert before.candles[-1].close == 10

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: _CompletedImporter(target_date),
    )

    def build_failed_qfq_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _PartiallyWrittenFailedImporter:
        source = (
            f"akshare_sina_daily_qfq:{version_tag}"
            if version_tag
            else "akshare_sina_daily_qfq"
        )
        return _PartiallyWrittenFailedImporter(target_date, source=source)

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        build_failed_qfq_importer,
    )

    with pytest.raises(CandleRefreshError, match="前复权股票日线未完整"):
        refresh_one_year_candles(
            database,
            requested_end_date=target_date,
        )

    after = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
    )
    assert after.actual_data_date == target_date
    assert after.candles[-1].close == 10
    _assert_no_staging_attempt_rows(database)


def test_failed_raw_rerun_keeps_last_successful_raw_candle_series(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "atomic-raw-rerun.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    published_qfq_source = "akshare_sina_daily_qfq:2026-07-24"
    with sqlite3.connect(database) as connection:
        for source in ("akshare_sina_daily", published_qfq_source):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, '2026-07-24', '600000', '浦发银行', 10, 10, 10, 10, 10, 0, 100, 1000, 100)
                """,
                (source,),
            )
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low,
                close, previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES ('akshare_sina_daily', '2026-07-23', '600000', '浦发银行',
                      10, 10, 10, 10, 10, 0, 100, 1000, 99)
            """
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', ?, '2026-07-24T16:30:00+08:00')
            """,
            (published_qfq_source,),
        )

    before = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    assert before.candles[-1].close == 10

    def build_failed_raw_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _PartiallyWrittenFailedImporter:
        source = (
            f"akshare_sina_daily:{version_tag}"
            if version_tag
            else "akshare_sina_daily"
        )
        return _PartiallyWrittenFailedImporter(target_date, source=source)

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        build_failed_raw_importer,
    )

    with pytest.raises(CandleRefreshError, match="不复权股票日线未完整"):
        refresh_one_year_candles(
            database,
            requested_end_date=target_date,
        )

    after = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    assert after.actual_data_date == target_date
    assert after.candles[-1].close == 10


def test_successful_rerun_atomically_replaces_published_raw_and_qfq_series(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "atomic-successful-rerun.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    published_qfq_source = "akshare_sina_daily_qfq:2026-07-24"
    with sqlite3.connect(database) as connection:
        for source in ("akshare_sina_daily", published_qfq_source):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, '2026-07-24', '600000', '浦发银行', 10, 10, 10, 10, 10, 0, 100, 1000, 100)
                """,
                (source,),
            )
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low,
                close, previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES ('akshare_sina_daily', '2026-07-23', '600000', '浦发银行',
                      10, 10, 10, 10, 10, 0, 100, 1000, 99)
            """
        )
        connection.execute(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES ('akshare_sina_daily', '2026-07-24', '沪深 300', 10, 10, 10, 10, 100)
            """
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', ?, '2026-07-24T16:30:00+08:00')
            """,
            (published_qfq_source,),
        )

    def build_raw_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _CompletedWritingImporter:
        source = (
            f"akshare_sina_daily:{version_tag}"
            if version_tag
            else "akshare_sina_daily"
        )
        return _CompletedWritingImporter(target_date, source=source, close=20)

    def build_qfq_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _CompletedWritingImporter:
        source = (
            f"akshare_sina_daily_qfq:{version_tag}"
            if version_tag
            else "akshare_sina_daily_qfq"
        )
        return _CompletedWritingImporter(target_date, source=source, close=21)

    def import_indexes(
        path: Path,
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> dict[str, int]:
        with sqlite3.connect(path) as connection:
            for symbol, name in INDEXES.items():
                source = index_source(symbol)
                if version_tag:
                    source = f"{source}:{version_tag}"
                connection.execute(
                    """
                    INSERT INTO historical_benchmark_facts (
                        source, actual_data_date, name, open, high, low, close, volume
                    ) VALUES (?, '2026-07-24', ?, 20, 20, 20, 20, 200)
                    """,
                    (source, name),
                )
        return {symbol: 1 for symbol in INDEXES}

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        build_raw_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        build_qfq_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        import_indexes,
    )

    summary = refresh_one_year_candles(
        database,
        requested_end_date=target_date,
        security_daily_fact_snapshot=lambda: pd.DataFrame(
            [{"代码": "600000", "名称": "浦发银行", "成交量": 100}]
        ),
    )

    raw = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    qfq = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
    )
    with sqlite3.connect(database) as connection:
        publication = connection.execute(
            """
            SELECT qfq_source FROM candle_dataset_publications
            WHERE actual_data_date = '2026-07-24'
            """
        ).fetchone()

    assert summary.actual_data_date == target_date
    assert raw.candles[-1].close == 20
    assert qfq.candles[-1].close == 21
    assert publication == (published_qfq_source,)
    _assert_no_staging_attempt_rows(database)


def test_first_refresh_publishes_from_staging_without_existing_canonical_data(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "first-atomic-refresh.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    trading_dates = [
        target_date - timedelta(days=offset)
        for offset in range(59, -1, -1)
    ]

    def build_raw_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _CompletedRangeWritingImporter:
        assert version_tag is not None
        return _CompletedRangeWritingImporter(
            trading_dates,
            source=f"akshare_sina_daily:{version_tag}",
            close=20,
        )

    def build_qfq_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _CompletedRangeWritingImporter:
        assert version_tag is not None
        return _CompletedRangeWritingImporter(
            trading_dates,
            source=f"akshare_sina_daily_qfq:{version_tag}",
            close=21,
        )

    def import_indexes(
        path: Path,
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> dict[str, int]:
        assert version_tag is not None
        with sqlite3.connect(path) as connection:
            for symbol, name in INDEXES.items():
                connection.executemany(
                    """
                    INSERT INTO historical_benchmark_facts (
                        source, actual_data_date, name, open, high, low, close, volume
                    ) VALUES (?, ?, ?, 20, 20, 20, 20, 200)
                    """,
                    [
                        (
                            index_source(symbol, version_tag=version_tag),
                            trading_date.isoformat(),
                            name,
                        )
                        for trading_date in trading_dates
                    ],
                )
        return {symbol: len(trading_dates) for symbol in INDEXES}

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        build_raw_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        build_qfq_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        import_indexes,
    )

    summary = refresh_one_year_candles(
        database,
        requested_end_date=target_date,
        security_daily_fact_snapshot=lambda: pd.DataFrame(
            [{"代码": "600000", "名称": "浦发银行", "成交量": 100}]
        ),
    )

    raw = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    qfq = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
    )
    assert summary.actual_data_date == target_date
    assert raw.candles[-1].close == 20
    assert qfq.candles[-1].close == 21
    _assert_no_staging_attempt_rows(database)


def test_coverage_does_not_borrow_raw_rows_from_last_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "missing-staging-raw.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    published_qfq_source = "akshare_sina_daily_qfq:2026-07-24"
    with sqlite3.connect(database) as connection:
        for source in ("akshare_sina_daily", published_qfq_source):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, '2026-07-24', '600000', '浦发银行',
                          10, 10, 10, 10, 10, 0, 100, 1000, 100)
                """,
                (source,),
            )
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low,
                close, previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES ('akshare_sina_daily', '2026-07-23', '600000', '浦发银行',
                      10, 10, 10, 10, 10, 0, 100, 1000, 99)
            """
        )
        connection.execute(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES ('akshare_sina_daily', '2026-07-24', '沪深 300',
                      10, 10, 10, 10, 100)
            """
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', ?, '2026-07-24T16:30:00+08:00')
            """,
            (published_qfq_source,),
        )

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **_: _CompletedImporter(target_date),
    )

    def build_qfq_importer(
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> _CompletedWritingImporter:
        assert version_tag is not None
        return _CompletedWritingImporter(
            target_date,
            source=f"akshare_sina_daily_qfq:{version_tag}",
            close=21,
        )

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        build_qfq_importer,
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        lambda *_, **__: pytest.fail("覆盖校验失败后不应导入指数"),
    )

    with pytest.raises(
        CandleRefreshError,
        match="目标交易日个股日频事实缺失：600000",
    ):
        refresh_one_year_candles(
            database,
            requested_end_date=target_date,
            security_daily_fact_snapshot=lambda: pd.DataFrame(
                [{"代码": "600000", "名称": "浦发银行", "成交量": 100}]
            ),
        )

    preserved = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    assert preserved.candles[-1].close == 10
    _assert_no_staging_attempt_rows(database)


def test_failed_publication_rolls_back_promoted_rows_and_keeps_last_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "failed-atomic-promotion.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    published_qfq_source = "akshare_sina_daily_qfq:2026-07-24"
    with sqlite3.connect(database) as connection:
        for source in ("akshare_sina_daily", published_qfq_source):
            connection.execute(
                """
                INSERT INTO historical_security_facts (
                    source, actual_data_date, code, name, open, high, low,
                    close, previous_close, change_pct, volume, turnover_cny,
                    listing_trading_days
                ) VALUES (?, '2026-07-24', '600000', '浦发银行',
                          10, 10, 10, 10, 10, 0, 100, 1000, 100)
                """,
                (source,),
            )
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low,
                close, previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES ('akshare_sina_daily', '2026-07-23', '600000', '浦发银行',
                      10, 10, 10, 10, 10, 0, 100, 1000, 99)
            """
        )
        connection.execute(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES ('akshare_sina_daily', '2026-07-24', '沪深 300',
                      10, 10, 10, 10, 100)
            """
        )
        for symbol, name in INDEXES.items():
            connection.execute(
                """
                INSERT INTO historical_benchmark_facts (
                    source, actual_data_date, name, open, high, low, close, volume
                ) VALUES (?, '2026-07-24', ?, 10, 10, 10, 10, 100)
                """,
                (index_source(symbol), name),
            )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', ?, '2026-07-24T16:30:00+08:00')
            """,
            (published_qfq_source,),
        )

    def build_importer(
        *,
        version_tag: str | None = None,
        qfq: bool = False,
        **_: object,
    ) -> _CompletedWritingImporter:
        assert version_tag is not None
        prefix = "akshare_sina_daily_qfq" if qfq else "akshare_sina_daily"
        return _CompletedWritingImporter(
            target_date,
            source=f"{prefix}:{version_tag}",
            close=21 if qfq else 20,
        )

    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_one_year_history_importer",
        lambda **kwargs: build_importer(**kwargs),
    )
    monkeypatch.setattr(
        candle_refresh_module,
        "build_akshare_qfq_history_importer",
        lambda **kwargs: build_importer(qfq=True, **kwargs),
    )

    def import_stale_indexes(
        path: Path,
        *,
        version_tag: str | None = None,
        **_: object,
    ) -> dict[str, int]:
        assert version_tag is not None
        with sqlite3.connect(path) as connection:
            for symbol, name in INDEXES.items():
                connection.execute(
                    """
                    INSERT INTO historical_benchmark_facts (
                        source, actual_data_date, name, open, high, low, close, volume
                    ) VALUES (?, '2026-07-23', ?, 20, 20, 20, 20, 200)
                    """,
                    (index_source(symbol, version_tag=version_tag), name),
                )
        return {symbol: 1 for symbol in INDEXES}

    monkeypatch.setattr(
        candle_refresh_module,
        "import_index_candles",
        import_stale_indexes,
    )

    with pytest.raises(CandleDataNotFound, match="指数日线"):
        refresh_one_year_candles(
            database,
            requested_end_date=target_date,
            security_daily_fact_snapshot=lambda: pd.DataFrame(
                [{"代码": "600000", "名称": "浦发银行", "成交量": 100}]
            ),
        )

    raw = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
        adjustment="raw",
    )
    qfq = read_candle_series(
        database,
        instrument_type="stock",
        code="600000",
        requested_date=target_date,
    )
    assert raw.candles[-1].close == 10
    assert qfq.candles[-1].close == 10
    _assert_no_staging_attempt_rows(database)


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


def test_refresh_rejects_missing_stock_without_mutating_last_success(
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
    assert {row[0] for row in stale_progress} == {
        "akshare_sina_daily",
        "akshare_sina_daily_qfq:2026-07-24",
    }
    assert stale_publication == (1,)


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


def test_refresh_keeps_existing_publication_when_coverage_snapshot_is_unusable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "unusable-coverage.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES ('2026-07-24', 'akshare_sina_daily_qfq:2026-07-24', 'old')
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
        lambda **_: _CompletedImporter(date(2026, 7, 24)),
    )

    with pytest.raises(CandleRefreshError, match="覆盖校验数据不可用"):
        refresh_one_year_candles(
            database,
            requested_end_date=date(2026, 7, 24),
            security_daily_fact_snapshot=pd.DataFrame,
        )

    with sqlite3.connect(database) as connection:
        publication = connection.execute(
            """
            SELECT 1 FROM candle_dataset_publications
            WHERE actual_data_date = '2026-07-24'
            """
        ).fetchone()
    assert publication == (1,)


def test_coverage_ignores_stocks_still_inside_new_stock_exclusion_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "new-stock-window.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES ('akshare_sina_daily', ?, '沪深300', 1, 1, 1, 1, 1)
            """,
            [("2026-07-23",), ("2026-07-24",)],
        )
        connection.executemany(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (?, ?, ?, ?, 10, 11, 9, 10, 10, 0, 100, 1000, ?)
            """,
            [
                ("akshare_sina_daily", "2026-07-23", "600000", "浦发银行", 100),
                ("akshare_sina_daily", "2026-07-24", "600000", "浦发银行", 101),
                (
                    "akshare_sina_daily_qfq:2026-07-24",
                    "2026-07-24",
                    "600000",
                    "浦发银行",
                    101,
                ),
            ],
        )

    candle_refresh_module._validate_security_daily_fact_coverage(
        database,
        requested_end_date=date(2026, 7, 24),
        raw_range_start=date(2025, 7, 24),
        qfq_range_start=date(2025, 7, 24),
        qfq_source="akshare_sina_daily_qfq:2026-07-24",
        new_stock_exclusion_days=60,
        listing_dates={"001237": date(2026, 6, 18)},
        snapshot=pd.DataFrame(
            [
                {"代码": "001237", "名称": "惠康科技", "成交量": 100},
                {"代码": "600000", "名称": "浦发银行", "成交量": 100},
            ]
        ),
    )


def test_coverage_requires_stock_on_first_eligible_trading_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "first-eligible-day.db"
    initialize_database(database)
    first_trading_day = date(2026, 5, 26)
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO historical_benchmark_facts (
                source, actual_data_date, name, open, high, low, close, volume
            ) VALUES ('akshare_sina_daily', ?, '沪深300', 1, 1, 1, 1, 1)
            """,
            [
                ((first_trading_day + timedelta(days=offset)).isoformat(),)
                for offset in range(60)
            ],
        )

    with pytest.raises(CandleRefreshError, match="001237"):
        candle_refresh_module._validate_security_daily_fact_coverage(
            database,
            requested_end_date=date(2026, 7, 24),
            raw_range_start=date(2025, 7, 24),
            qfq_range_start=date(2025, 7, 24),
            qfq_source="akshare_sina_daily_qfq:2026-07-24",
            new_stock_exclusion_days=60,
            listing_dates={"001237": first_trading_day},
            snapshot=pd.DataFrame(
                [{"代码": "001237", "名称": "惠康科技", "成交量": 100}]
            ),
        )
