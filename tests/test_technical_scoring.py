from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.candlesticks import INDEXES, index_source
from fourseasquant.database import (
    HistoricalBenchmarkFactRow,
    HistoricalSecurityFactRow,
    initialize_database,
    save_historical_benchmark_fact,
    save_history_symbol_batch,
)
from fourseasquant.technical_scoring import (
    ALGORITHM_VERSION,
    TechnicalParameters,
    read_technical_score_status,
    read_top_technical_scores,
    score_technical_history,
)


def test_score_history_publishes_versioned_derivative_extrema_and_scores(
    tmp_path: Path,
) -> None:
    database = tmp_path / "technical.db"
    initialize_database(database)
    start = date(2026, 1, 1)
    dates = [start + timedelta(days=index) for index in range(36)]
    closes = [
        10.0,
        10.5,
        11.0,
        10.4,
        10.0,
        10.8,
        11.6,
        11.0,
        10.7,
        11.5,
        12.2,
        11.6,
        11.2,
        12.0,
        12.8,
        12.1,
        11.8,
        12.7,
        13.5,
        12.8,
        12.4,
        13.4,
        14.2,
        13.6,
        13.1,
        14.0,
        14.8,
        14.2,
        13.8,
        14.7,
        15.5,
        15.0,
        14.6,
        15.4,
        16.1,
        16.8,
    ]
    qfq_source = "akshare_sina_daily_qfq:test"
    facts: list[HistoricalSecurityFactRow] = []
    for index, (trading_date, close) in enumerate(zip(dates, closes, strict=True)):
        previous = closes[index - 1] if index else close
        facts.append(
            HistoricalSecurityFactRow(
                actual_data_date=trading_date,
                code="300001",
                name="成长样本",
                open=previous,
                high=max(previous, close) + 0.2,
                low=min(previous, close) - 0.2,
                close=close,
                previous_close=previous,
                change_pct=(close / previous - 1) * 100 if previous else 0,
                volume=1_000_000,
                turnover_cny=100_000_000 + index * 2_000_000,
                listing_trading_days=100 + index,
            )
        )
        for symbol, name in INDEXES.items():
            benchmark_close = 1_000 + index
            save_historical_benchmark_fact(
                database,
                source=index_source(symbol),
                fact=HistoricalBenchmarkFactRow(
                    actual_data_date=trading_date,
                    name=name,
                    open=benchmark_close,
                    high=benchmark_close + 2,
                    low=benchmark_close - 2,
                    close=benchmark_close,
                    volume=1_000_000,
                ),
            )
    save_history_symbol_batch(
        database,
        source=qfq_source,
        range_start=dates[0],
        range_end=dates[-1],
        code="300001",
        facts=facts,
        completed_at=datetime(
            2026, 2, 10, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )

    publication = score_technical_history(
        database,
        official_start=dates[10],
        official_end=dates[-1],
        qfq_source=qfq_source,
    )

    assert publication.version == ALGORITHM_VERSION
    assert publication.symbol_count == 1
    assert publication.score_count == 26
    status = read_technical_score_status(database)
    assert status.status == "ready"
    assert status.publication == publication
    top = read_top_technical_scores(
        database, requested_date=dates[-1], limit=5
    )
    assert len(top) == 1
    assert top[0].board == "chinext"
    assert top[0].ema3 > 0
    assert top[0].atr10 > 0
    assert top[0].derivative_state == "positive"
    assert top[0].maxima
    assert top[0].minima
    assert top[0].structure_valid is True
    assert top[0].total_score == pytest.approx(
        top[0].structure_score
        + top[0].breakout_score
        + top[0].relative_strength_score
        + top[0].turnover_score,
        abs=0.0002,
    )
    with pytest.raises(
        ValueError, match="同一技术算法版本不允许修改参数"
    ):
        score_technical_history(
            database,
            official_start=dates[10],
            official_end=dates[-1],
            qfq_source=qfq_source,
            parameters=TechnicalParameters(ema_span=5),
            version=ALGORITHM_VERSION,
        )


def test_score_status_is_explicit_before_initialization(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    initialize_database(database)

    status = read_technical_score_status(database)

    assert status.status == "not_initialized"
    assert status.publication is None
    assert read_top_technical_scores(
        database, requested_date=date(2026, 7, 23)
    ) == []
