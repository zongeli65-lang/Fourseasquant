from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fourseasquant.akshare_market_data import (
    BenchmarkDailyFact,
    DailyMarketFacts,
    SecurityDailyFact,
)
from fourseasquant.main import app


def test_market_data_collection_endpoint_returns_akshare_batch_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    facts = DailyMarketFacts(
        source="akshare",
        requested_date=date(2026, 7, 22),
        actual_data_date=date(2026, 7, 21),
        benchmark=BenchmarkDailyFact(
            name="沪深 300",
            date=date(2026, 7, 21),
            open=4700,
            high=4750,
            low=4680,
            close=4739.229,
            volume=30_000_000_000,
        ),
        securities=[
            SecurityDailyFact(
                code="000001",
                name="平安银行",
                date=date(2026, 7, 21),
                open=12.1,
                high=12.2,
                low=11.9,
                close=12.0,
                previous_close=12.06,
                change_pct=-0.5,
                volume=2_000_000,
                turnover_cny=24_000_000,
                listing_trading_days=8_000,
            )
        ],
    )
    monkeypatch.setattr(
        "fourseasquant.main.collect_and_store_daily_facts",
        lambda *args, **kwargs: facts,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/market-data/daily",
            json={"target_date": "2026-07-22"},
        )

    assert response.status_code == 201
    assert response.json() == {
        "source": "akshare",
        "requested_date": "2026-07-22",
        "actual_data_date": "2026-07-21",
        "benchmark": "沪深 300",
        "benchmark_close": 4739.229,
        "security_count": 1,
    }
