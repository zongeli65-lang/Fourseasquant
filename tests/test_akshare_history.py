from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.akshare_history import AkshareOneYearHistoryImporter
from fourseasquant.database import (
    completed_history_symbols,
    initialize_database,
    latest_market_facts,
)


def _index_history() -> pd.DataFrame:
    dates = pd.bdate_range("2025-06-01", "2026-07-21")
    return pd.DataFrame(
        {
            "date": dates.date,
            "open": 4700.0,
            "high": 4750.0,
            "low": 4680.0,
            "close": 4739.229,
            "volume": 30_000_000_000,
        }
    )


def _history(close_offset: float = 0.0) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for offset, timestamp in enumerate(
        pd.bdate_range("2025-07-21", "2026-07-21")
    ):
        close = 10.1 + close_offset + offset / 1_000
        trading_date = timestamp.date()
        rows.append(
            {
                "date": trading_date,
                "open": close - 0.1,
                "high": close + 0.1,
                "low": close - 0.2,
                "close": close,
                "volume": 1_200_000,
                "amount": (
                    12_300_000
                    if trading_date == date(2025, 7, 22)
                    else 12_000_000
                ),
            }
        )
    return pd.DataFrame(rows)


def _importer(
    stock_history: Callable[[str, date, date], pd.DataFrame],
) -> AkshareOneYearHistoryImporter:
    return AkshareOneYearHistoryImporter(
        sh_listing=lambda: pd.DataFrame(
            [
                {
                    "证券代码": "600000",
                    "证券简称": "浦发银行",
                    "上市日期": date(1999, 11, 10),
                },
                {
                    "证券代码": "600001",
                    "证券简称": "ST示例",
                    "上市日期": date(2000, 1, 1),
                },
            ]
        ),
        sz_listing=lambda: pd.DataFrame(
            [
                {
                    "板块": "主板",
                    "A股代码": "000001",
                    "A股简称": "平安银行",
                    "A股上市日期": date(1991, 4, 3),
                },
                {
                    "板块": "创业板",
                    "A股代码": "300001",
                    "A股简称": "创业样本",
                    "A股上市日期": date(2010, 1, 1),
                },
            ]
        ),
        index_history=_index_history,
        stock_history=stock_history,
        max_workers=2,
    )


def test_one_year_import_uses_exact_amount_and_publishes_complete_days(
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.db"
    initialize_database(database)
    calls: list[str] = []

    def stock_history(symbol: str, start: date, end: date) -> pd.DataFrame:
        calls.append(symbol)
        return _history(0.1 if symbol == "sh600000" else 0.0)

    summary = _importer(stock_history).import_one_year(
        path=database,
        requested_end_date=date(2026, 7, 22),
        new_stock_exclusion_days=20,
        collected_at=datetime(
            2026, 7, 22, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )

    assert summary.range_start == date(2025, 7, 22)
    assert summary.range_end == date(2026, 7, 21)
    assert summary.total_symbols == 2
    assert summary.completed_symbols == 2
    assert summary.failed_codes == []
    assert set(calls) == {"sh600000", "sz000001"}
    first_day = latest_market_facts(database, date(2025, 7, 22))
    assert first_day is not None
    assert '"turnover_cny":12300000' in first_day.payload_json
    assert '"previous_close":10.1' in first_day.payload_json
    assert "ST示例" not in first_day.payload_json
    assert "创业样本" not in first_day.payload_json


def test_one_year_import_resumes_completed_symbols_before_publishing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "resume.db"
    initialize_database(database)
    first_calls: list[str] = []

    def interrupted_history(symbol: str, start: date, end: date) -> pd.DataFrame:
        first_calls.append(symbol)
        if symbol == "sh600000":
            raise ConnectionError("临时断开")
        return _history()

    first = _importer(interrupted_history).import_one_year(
        path=database,
        requested_end_date=date(2026, 7, 22),
        new_stock_exclusion_days=20,
    )
    assert first.failed_codes == ["600000"]
    assert first.published_days == 0
    assert latest_market_facts(database, date(2025, 7, 22)) is None

    resumed_calls: list[str] = []

    def resumed_history(symbol: str, start: date, end: date) -> pd.DataFrame:
        resumed_calls.append(symbol)
        return _history()

    resumed = _importer(resumed_history).import_one_year(
        path=database,
        requested_end_date=date(2026, 7, 22),
        new_stock_exclusion_days=20,
    )

    assert resumed.failed_codes == []
    assert resumed.completed_symbols == 2
    assert resumed_calls == ["sh600000"]
    assert latest_market_facts(database, date(2025, 7, 22)) is not None
    assert completed_history_symbols(
        database,
        source="akshare_sina_daily",
        range_start=date(2025, 7, 22),
        range_end=date(2026, 7, 21),
    ) == {"000001", "600000"}
