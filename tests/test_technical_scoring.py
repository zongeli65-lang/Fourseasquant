from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import fourseasquant.technical_scoring as technical_scoring
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
    DEFAULT_PARAMETERS,
    TechnicalParameters,
    read_technical_score_page,
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


def test_structure_breaks_after_ema3_falls_two_percent_below_last_trough() -> None:
    start = date(2026, 1, 1)
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
        15.0,
        13.5,
        12.0,
        10.5,
        9.0,
    ]
    dates = [start + timedelta(days=index) for index in range(len(closes))]
    rows = [
        technical_scoring._PriceRow(
            trading_date=trading_date,
            code="603773",
            name="破位样本",
            open=closes[index - 1] if index else close,
            high=max(closes[index - 1] if index else close, close) + 0.2,
            low=min(closes[index - 1] if index else close, close) - 0.2,
            close=close,
            previous_close=closes[index - 1] if index else close,
            turnover_cny=100_000_000,
        )
        for index, (trading_date, close) in enumerate(
            zip(dates, closes, strict=True)
        )
    ]
    benchmark_closes = {
        symbol: {
            trading_date: 1_000.0 + index
            for index, trading_date in enumerate(dates)
        }
        for symbol in ("sh000300", "sh000001")
    }

    scores = technical_scoring._score_symbol(
        rows,
        benchmark_closes=benchmark_closes,
        official_start=dates[0],
        official_end=dates[-1],
        qfq_source="test-qfq",
        parameters=DEFAULT_PARAMETERS,
        version=ALGORITHM_VERSION,
    )

    assert any(score.structure_valid for score in scores[:-5])
    broken = scores[-1]
    assert broken.structure_state == "broken"
    assert broken.structure_valid is False
    assert broken.structure_score == 0
    assert broken.breakout_score == 0
    assert broken.evidence["structure_break_reason"] == "ema_below_last_trough"
    observation_breakouts = [
        score
        for score in scores
        if score.active_breakout and not score.structure_valid
    ]
    assert observation_breakouts
    assert all(score.breakout_score == 0 for score in observation_breakouts)


def test_extrema_lifts_do_not_change_with_later_atr() -> None:
    extrema = [
        technical_scoring._Extremum(
            kind="minimum",
            trading_date=date(2026, 1, 1),
            value=10.0,
            atr10=1.0,
        ),
        technical_scoring._Extremum(
            kind="minimum",
            trading_date=date(2026, 1, 10),
            value=12.0,
            atr10=2.0,
        ),
    ]

    high_current_atr = technical_scoring._normalized_lifts(extrema, 10.0)
    low_current_atr = technical_scoring._normalized_lifts(extrema, 0.1)

    assert high_current_atr == low_current_atr == [1.0]


def test_score_status_is_explicit_before_initialization(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    initialize_database(database)

    status = read_technical_score_status(database)

    assert status.status == "not_initialized"
    assert status.publication is None
    assert read_top_technical_scores(
        database, requested_date=date(2026, 7, 23)
    ) == []


def test_full_score_page_searches_all_symbols_and_separates_stale_scores(
    tmp_path: Path,
) -> None:
    database = tmp_path / "technical-page.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO technical_score_versions (
                version, parameters_json, created_at
            ) VALUES (?, ?, ?)
            """,
            (
                ALGORITHM_VERSION,
                DEFAULT_PARAMETERS.model_dump_json(),
                "2026-07-24T16:30:00+08:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO technical_score_publications (
                version, official_start, official_end, qfq_source,
                symbol_count, score_count, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ALGORITHM_VERSION,
                "2026-07-01",
                "2026-07-24",
                "test-qfq",
                3,
                3,
                "2026-07-24T16:30:00+08:00",
            ),
        )
        _insert_score_row(
            connection,
            trading_date="2026-07-24",
            code="600001",
            name="当前高分",
            board="main",
            total_score=90.0,
        )
        _insert_score_row(
            connection,
            trading_date="2026-07-24",
            code="300001",
            name="当前次高",
            board="chinext",
            total_score=80.0,
        )
        _insert_score_row(
            connection,
            trading_date="2026-07-23",
            code="688001",
            name="旧日样本",
            board="star",
            total_score=99.0,
        )

    first_page = read_technical_score_page(
        database,
        requested_date=date(2026, 7, 24),
        page_size=2,
    )
    stale_search = read_technical_score_page(
        database,
        requested_date=date(2026, 7, 24),
        search="旧日",
    )
    board_filter = read_technical_score_page(
        database,
        requested_date=date(2026, 7, 24),
        board="chinext",
    )

    assert first_page.total == 3
    assert first_page.universe_count == 3
    assert first_page.current_score_count == 2
    assert first_page.stale_score_count == 1
    assert [item.code for item in first_page.items] == ["600001", "300001"]
    assert [item.rank for item in first_page.items] == [1, 2]
    assert all(item.is_current for item in first_page.items)

    assert stale_search.total == 1
    assert stale_search.items[0].code == "688001"
    assert stale_search.items[0].actual_data_date == date(2026, 7, 23)
    assert stale_search.items[0].rank is None
    assert stale_search.items[0].is_current is False

    assert board_filter.total == 1
    assert board_filter.items[0].code == "300001"


def _insert_score_row(
    connection: sqlite3.Connection,
    *,
    trading_date: str,
    code: str,
    name: str,
    board: str,
    total_score: float,
) -> None:
    extrema = json.dumps(
        {
            "maxima": [
                {"kind": "maximum", "date": trading_date, "value": 12.0}
            ],
            "minima": [
                {"kind": "minimum", "date": trading_date, "value": 10.0}
            ],
        }
    )
    connection.execute(
        """
        INSERT INTO technical_daily_scores (
            version, actual_data_date, code, name, board, qfq_source,
            ema3, derivative, derivative_state, zero_threshold, atr10,
            structure_state, structure_valid, active_breakout,
            structure_score, breakout_score, relative_strength_score,
            turnover_score, total_score, extrema_json, evidence_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            ALGORITHM_VERSION,
            trading_date,
            code,
            name,
            board,
            "test-qfq",
            11.0,
            0.1,
            "positive",
            0.01,
            0.5,
            "strong",
            1,
            0,
            50.0,
            15.0,
            10.0,
            5.0,
            total_score,
            extrema,
            "{}",
        ),
    )
