import sqlite3
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import fourseasquant.core_strategy_adapters as adapters
from fourseasquant.core_strategy_adapters import (
    StrategyInputUnavailable,
    load_core_strategy_daily_inputs,
    load_position_observations,
    read_strategy_opinion_evidence,
)
from fourseasquant.core_strategy_positions import HoldingPosition
from fourseasquant.public_opinion_repository import PublicOpinionWindow


START = date(2026, 5, 30)
ACTUAL_DATE = START + timedelta(days=59)
QFQ_SOURCE = "test-qfq:2026-07-28"


def _create_minimal_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE technical_score_publications (
                version TEXT,
                official_start TEXT,
                official_end TEXT,
                qfq_source TEXT,
                published_at TEXT
            );
            CREATE TABLE candle_dataset_publications (
                actual_data_date TEXT,
                qfq_source TEXT
            );
            CREATE TABLE technical_daily_scores (
                version TEXT,
                qfq_source TEXT,
                actual_data_date TEXT,
                code TEXT,
                name TEXT,
                board TEXT,
                structure_score REAL,
                breakout_score REAL,
                relative_strength_score REAL,
                turnover_score REAL,
                derivative_state TEXT
            );
            CREATE TABLE historical_security_facts (
                source TEXT,
                actual_data_date TEXT,
                code TEXT,
                name TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                previous_close REAL,
                change_pct REAL,
                volume INTEGER,
                turnover_cny INTEGER,
                listing_trading_days INTEGER
            );
            """
        )
        connection.execute(
            """
            INSERT INTO technical_score_publications
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "technical-v3",
                START.isoformat(),
                ACTUAL_DATE.isoformat(),
                QFQ_SOURCE,
                "2026-07-28T16:00:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO candle_dataset_publications VALUES (?, ?)",
            (ACTUAL_DATE.isoformat(), QFQ_SOURCE),
        )
        previous_close = 10.0
        for offset in range(60):
            trading_date = START + timedelta(days=offset)
            center = 10 + (0.02 if offset % 2 == 0 else -0.02)
            close = center + (0.01 if offset % 2 == 0 else -0.01)
            common = (
                trading_date.isoformat(),
                "600001",
                "主板测试",
                center - 0.02,
                center + 0.05,
                center - 0.05,
                close,
                previous_close,
                (close / previous_close - 1) * 100,
                100_000,
                100_000_000,
                100 + offset,
            )
            connection.execute(
                """
                INSERT INTO historical_security_facts
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (QFQ_SOURCE, *common),
            )
            connection.execute(
                """
                INSERT INTO technical_daily_scores
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "technical-v3",
                    QFQ_SOURCE,
                    trading_date.isoformat(),
                    "600001",
                    "主板测试",
                    "main",
                    90,
                    10,
                    20,
                    30,
                    "zero",
                ),
            )
            previous_close = close
        connection.execute(
            """
            INSERT INTO historical_security_facts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "akshare_sina_daily",
                ACTUAL_DATE.isoformat(),
                "600001",
                "主板测试",
                10,
                10.1,
                9.9,
                10.01,
                10,
                0.1,
                100_000,
                100_000_000,
                159,
            ),
        )


def _market() -> SimpleNamespace:
    return SimpleNamespace(
        actual_data_date=ACTUAL_DATE,
        trend_state="sideways",
        rules_version="market-environment-v1",
        trend_id="trend-test",
    )


def test_loader_uses_same_day_published_facts_and_builds_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "strategy-inputs.db"
    _create_minimal_database(database)
    monkeypatch.setattr(
        adapters,
        "read_market_environment",
        lambda *_, **__: _market(),
    )

    result = load_core_strategy_daily_inputs(
        database,
        requested_date=ACTUAL_DATE,
        corporate_actions_complete=True,
    )

    assert result.actual_date == ACTUAL_DATE
    assert result.market_state == "sideways"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.preliminary.route == "pure_technical"
    assert candidate.preliminary.base_grade == "B"
    assert candidate.preliminary.preliminary_rank == 1
    assert candidate.technical is not None
    assert candidate.technical.status == "ready"
    assert result.input_versions.technical_scores is not None
    assert QFQ_SOURCE in result.input_versions.technical_scores

    positions = load_position_observations(
        database,
        daily_inputs=result,
        positions=[
            HoldingPosition(
                code="600001",
                name="主板测试",
                shares=100,
                cost_price=9,
                stop_price=8,
                pressure_target=12,
                initial_risk=1,
                highest_close_since_entry=10,
            )
        ],
    )
    assert positions.unavailable_codes == []
    assert positions.observations[0].code == "600001"
    assert positions.observations[0].limit_down_price == 9


def test_loader_rejects_technical_version_without_matching_candle_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "strategy-inputs.db"
    _create_minimal_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM candle_dataset_publications")
    monkeypatch.setattr(
        adapters,
        "read_market_environment",
        lambda *_, **__: _market(),
    )

    with pytest.raises(
        StrategyInputUnavailable,
        match="前复权 K 线",
    ):
        load_core_strategy_daily_inputs(
            database,
            requested_date=ACTUAL_DATE,
            corporate_actions_complete=True,
        )


def test_opinion_adapter_uses_shortest_published_window_without_mixing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_window(*_: object, **kwargs: object) -> PublicOpinionWindow:
        days = cast(int, kwargs["days"])
        calls.append(days)
        published = days == 3
        return PublicOpinionWindow(
            platform="sina",
            code=str(kwargs["code"]),
            start_date=ACTUAL_DATE - timedelta(days=days - 1),
            end_date=ACTUAL_DATE,
            expected_day_count=days,
            completed_day_count=days,
            content_count=12 if published else 2,
            valid_count=12 if published else 2,
            favorable_count=10 if published else 1,
            unfavorable_count=2 if published else 0,
            disputed_count=0,
            unknown_count=0,
            direction_index=0.6 if published else None,
            direction="favorable" if published else None,
            direction_status=(
                "published" if published else "insufficient_sample"
            ),
        )

    monkeypatch.setattr(
        adapters,
        "read_public_opinion_window",
        fake_window,
    )

    evidence = read_strategy_opinion_evidence(
        tmp_path / "unused.db",
        actual_date=ACTUAL_DATE,
        candidate_codes=["600001", "600002"],
        targeted_codes=["600001"],
        rules_version="opinion-test-v1",
    )

    assert calls == [1, 3]
    assert evidence[0].status == "favorable"
    assert evidence[0].window_days == 3
    assert evidence[1].status == "not_targeted"
