from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import (
    HistoricalBenchmarkFactRow,
    HistoricalMarketSummaryRow,
    HistoricalSecurityFactRow,
    initialize_database,
    save_historical_benchmark_fact,
    save_historical_market_summary,
    save_history_symbol_batch,
)
from fourseasquant.real_market_dashboard import read_real_market_dashboard


def _security(
    trading_date: date,
    code: str,
    name: str,
    change_pct: float,
    turnover_cny: int,
) -> HistoricalSecurityFactRow:
    previous_close = 10.0
    close = previous_close * (1 + change_pct / 100)
    return HistoricalSecurityFactRow(
        actual_data_date=trading_date,
        code=code,
        name=name,
        open=10.0,
        high=max(10.0, close),
        low=min(10.0, close),
        close=close,
        previous_close=previous_close,
        change_pct=change_pct,
        volume=1_000_000,
        turnover_cny=turnover_cny,
        listing_trading_days=1_000,
    )


def test_real_market_dashboard_uses_latest_available_date_and_compact_metrics(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dashboard.db"
    initialize_database(database)
    collected_at = datetime(
        2026, 7, 22, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    range_start = date(2025, 7, 22)
    range_end = date(2026, 7, 21)
    for trading_date, close in (
        (date(2026, 7, 20), 4700.0),
        (date(2026, 7, 21), 4739.229),
    ):
        save_historical_benchmark_fact(
            database,
            source="akshare_sina_daily",
            fact=HistoricalBenchmarkFactRow(
                actual_data_date=trading_date,
                name="沪深 300",
                open=close - 10,
                high=close + 10,
                low=close - 20,
                close=close,
                volume=30_000_000_000,
            ),
        )
    stock_rows = {
        "000001": [
            _security(date(2026, 7, 20), "000001", "平安银行", 1.0, 100_000_000),
            _security(date(2026, 7, 21), "000001", "平安银行", 2.0, 120_000_000),
        ],
        "600000": [
            _security(date(2026, 7, 20), "600000", "浦发银行", -1.0, 90_000_000),
            _security(date(2026, 7, 21), "600000", "浦发银行", -3.0, 80_000_000),
        ],
        "600519": [
            _security(date(2026, 7, 20), "600519", "贵州茅台", 0.0, 200_000_000),
            _security(date(2026, 7, 21), "600519", "贵州茅台", 0.0, 250_000_000),
        ],
    }
    for code, facts in stock_rows.items():
        save_history_symbol_batch(
            database,
            source="akshare_sina_daily",
            range_start=range_start,
            range_end=range_end,
            code=code,
            facts=facts,
            completed_at=collected_at,
        )
    for trading_date, benchmark_close, turnover in (
        (date(2026, 7, 20), 4700.0, 390_000_000),
        (date(2026, 7, 21), 4739.229, 450_000_000),
    ):
        save_historical_market_summary(
            database,
            source="akshare_sina_daily",
            summary=HistoricalMarketSummaryRow(
                actual_data_date=trading_date,
                benchmark_close=benchmark_close,
                turnover_cny=turnover,
                security_count=3,
                advancers=1,
                decliners=1,
                unchanged=1,
            ),
        )

    dashboard = read_real_market_dashboard(database, date(2026, 7, 22))

    assert dashboard.source == "akshare"
    assert dashboard.actual_data_date == date(2026, 7, 21)
    assert dashboard.coverage_start == date(2026, 7, 20)
    assert dashboard.coverage_end == date(2026, 7, 21)
    assert dashboard.benchmark.close == 4739.229
    assert round(dashboard.benchmark.change_pct, 4) == 0.8347
    assert dashboard.breadth.advancers == 1
    assert dashboard.breadth.decliners == 1
    assert dashboard.breadth.unchanged == 1
    assert dashboard.turnover_cny == 450_000_000
    assert dashboard.gainers[0].code == "000001"
    assert dashboard.losers[0].code == "600000"
    assert dashboard.heatmap[0].code == "600519"
    assert len(dashboard.trend) == 2
    assert dashboard.trend[-1].advancer_ratio == pytest.approx(100 / 3)
