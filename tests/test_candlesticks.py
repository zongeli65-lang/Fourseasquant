from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.akshare_history import HISTORY_QFQ_SOURCE, HISTORY_SOURCE
from fourseasquant.candlesticks import (
    INDEXES,
    index_source,
    publish_complete_candle_dates,
    read_candle_series,
    search_eligible_securities,
)
from fourseasquant.database import (
    HistoricalBenchmarkFactRow,
    HistoricalSecurityFactRow,
    initialize_database,
    save_history_symbol_batch,
    save_historical_benchmark_fact,
)


def _seed(path: Path) -> None:
    initialize_database(path)
    start = date(2026, 6, 30)
    days = [start + timedelta(days=offset) for offset in range(16)]
    facts: list[HistoricalSecurityFactRow] = []
    qfq: list[HistoricalSecurityFactRow] = []
    for offset, trading_date in enumerate(days):
        close = 10 + offset * 0.1
        fact = HistoricalSecurityFactRow(
            actual_data_date=trading_date,
            code="600000",
            name="浦发银行",
            open=close - 0.05,
            high=close + 0.1,
            low=close - 0.1,
            close=close,
            previous_close=close - 0.1 if offset else close,
            change_pct=0.9,
            volume=1_000_000 + offset,
            turnover_cny=10_000_000 + offset,
            listing_trading_days=5_000 + offset,
        )
        facts.append(fact)
        qfq.append(
            HistoricalSecurityFactRow(
                **{**fact.__dict__, "open": fact.open / 2, "high": fact.high / 2,
                   "low": fact.low / 2, "close": fact.close / 2,
                   "previous_close": fact.previous_close / 2}
            )
        )
        for symbol, name in INDEXES.items():
            save_historical_benchmark_fact(
                path,
                source=index_source(symbol),
                fact=HistoricalBenchmarkFactRow(
                    actual_data_date=trading_date,
                    name=name,
                    open=3_000 + offset,
                    high=3_010 + offset,
                    low=2_990 + offset,
                    close=3_005 + offset,
                    volume=100_000_000,
                ),
            )
    completed_at = datetime(2026, 7, 22, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    for source, rows in ((HISTORY_SOURCE, facts), (HISTORY_QFQ_SOURCE, qfq)):
        save_history_symbol_batch(
            path,
            source=source,
            range_start=days[0],
            range_end=days[-1],
            code="600000",
            facts=rows,
            completed_at=completed_at,
        )


def test_complete_dataset_enables_search_and_qfq_candles(tmp_path: Path) -> None:
    path = tmp_path / "candles.db"
    _seed(path)
    assert publish_complete_candle_dates(path) == 16

    results = search_eligible_securities(
        path,
        query="浦发",
        requested_date=date(2026, 7, 22),
    )
    assert [item.code for item in results] == ["600000"]

    series = read_candle_series(
        path,
        instrument_type="stock",
        code="600000",
        requested_date=date(2026, 7, 22),
        adjustment="qfq",
    )
    assert series.actual_data_date == date(2026, 7, 15)
    assert series.candles[-1].close == 5.75
    assert series.candles[13].rsi14 is None
    assert series.candles[14].rsi14 == 100


def test_index_is_raw_and_respects_requested_date(tmp_path: Path) -> None:
    path = tmp_path / "index.db"
    _seed(path)
    publish_complete_candle_dates(path)
    series = read_candle_series(
        path,
        instrument_type="index",
        code="sh000001",
        requested_date=date(2026, 7, 10),
        adjustment="qfq",
    )
    assert series.name == "上证指数"
    assert series.adjustment == "raw"
    assert series.actual_data_date == date(2026, 7, 10)
    assert series.coverage_end == date(2026, 7, 10)


def test_no_publication_hides_partial_dataset(tmp_path: Path) -> None:
    path = tmp_path / "partial.db"
    initialize_database(path)
    assert search_eligible_securities(
        path, query="600000", requested_date=date(2026, 7, 22)
    ) == []
