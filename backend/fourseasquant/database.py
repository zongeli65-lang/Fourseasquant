from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class SnapshotRow:
    actual_data_date: date
    payload_json: str
    published_at: datetime


@dataclass(frozen=True)
class TaskRunRow:
    id: int
    trigger_method: str
    target_date: date
    started_at: datetime
    finished_at: datetime | None
    stage: str
    status: str
    error_summary: str | None


@dataclass(frozen=True)
class MarketFactsRow:
    id: int
    actual_data_date: date
    source: str
    payload_json: str
    collected_at: datetime


@dataclass(frozen=True)
class HistoricalSecurityFactRow:
    actual_data_date: date
    code: str
    name: str
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    change_pct: float
    volume: int
    turnover_cny: int
    listing_trading_days: int


@dataclass(frozen=True)
class HistoricalBenchmarkFactRow:
    actual_data_date: date
    name: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class HistoricalMarketSummaryRow:
    actual_data_date: date
    benchmark_close: float
    turnover_cny: int
    security_count: int
    advancers: int
    decliners: int
    unchanged: int


def database_path() -> Path:
    configured_path = os.environ.get("FOURSEASQUANT_DB_PATH")
    if configured_path:
        return Path(configured_path)
    return Path("data/fourseasquant.db")


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_method TEXT NOT NULL,
                target_date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'queued',
                error_summary TEXT
            )
            """
        )
        task_columns = {
            cast(str, row[1])
            for row in connection.execute("PRAGMA table_info(task_runs)")
        }
        if "stage" not in task_columns:
            connection.execute(
                "ALTER TABLE task_runs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'"
            )
        if "error_summary" not in task_columns:
            connection.execute(
                "ALTER TABLE task_runs ADD COLUMN error_summary TEXT"
            )
        connection.execute(
            """
            UPDATE task_runs
            SET stage = CASE
                WHEN status = 'succeeded' THEN 'completed'
                WHEN status = 'failed' THEN 'unknown'
                ELSE stage
            END
            WHERE stage = 'queued' AND status != 'running'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                target_date TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                published_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_fact_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actual_data_date TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                UNIQUE(actual_data_date, content_sha256)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_fact_snapshots_date_id
            ON market_fact_snapshots (actual_data_date, id DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_security_facts (
                source TEXT NOT NULL,
                actual_data_date TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                previous_close REAL NOT NULL,
                change_pct REAL NOT NULL,
                volume INTEGER NOT NULL,
                turnover_cny INTEGER NOT NULL,
                listing_trading_days INTEGER NOT NULL,
                PRIMARY KEY (source, actual_data_date, code)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_historical_security_facts_date
            ON historical_security_facts (source, actual_data_date, code)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history_ingestion_progress (
                source TEXT NOT NULL,
                range_start TEXT NOT NULL,
                range_end TEXT NOT NULL,
                code TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (source, range_start, range_end, code)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_benchmark_facts (
                source TEXT NOT NULL,
                actual_data_date TEXT NOT NULL,
                name TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                PRIMARY KEY (source, actual_data_date)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_market_daily_summary (
                source TEXT NOT NULL,
                actual_data_date TEXT NOT NULL,
                benchmark_close REAL NOT NULL,
                turnover_cny INTEGER NOT NULL,
                security_count INTEGER NOT NULL,
                advancers INTEGER NOT NULL,
                decliners INTEGER NOT NULL,
                unchanged INTEGER NOT NULL,
                PRIMARY KEY (source, actual_data_date)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candle_dataset_publications (
                actual_data_date TEXT PRIMARY KEY,
                qfq_source TEXT NOT NULL DEFAULT 'akshare_sina_daily_qfq',
                published_at TEXT NOT NULL
            )
            """
        )
        candle_publication_columns = {
            cast(str, row[1])
            for row in connection.execute(
                "PRAGMA table_info(candle_dataset_publications)"
            )
        }
        if "qfq_source" not in candle_publication_columns:
            connection.execute(
                """
                ALTER TABLE candle_dataset_publications
                ADD COLUMN qfq_source TEXT NOT NULL
                DEFAULT 'akshare_sina_daily_qfq'
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_claims (
                target_date TEXT PRIMARY KEY,
                claimed_at TEXT NOT NULL,
                claim_id TEXT NOT NULL
            )
            """
        )
        claim_columns = {
            cast(str, row[1])
            for row in connection.execute("PRAGMA table_info(automation_claims)")
        }
        if "claim_id" not in claim_columns:
            connection.execute("ALTER TABLE automation_claims ADD COLUMN claim_id TEXT")
            connection.execute(
                "UPDATE automation_claims SET claim_id = lower(hex(randomblob(16))) WHERE claim_id IS NULL"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reviews (
                review_date TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                auto_update_time TEXT NOT NULL,
                benchmark TEXT NOT NULL,
                data_adapter TEXT NOT NULL,
                new_stock_exclusion_days INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO app_settings (
                id,
                auto_update_time,
                benchmark,
                data_adapter,
                new_stock_exclusion_days
            ) VALUES (1, '16:30', '沪深 300', 'simulation', 60)
            """
        )
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES ('schema_version', '9')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )


def database_is_ready(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as connection:
            row = cast(
                tuple[str] | None,
                connection.execute(
                    "SELECT value FROM app_metadata WHERE key = 'schema_version'"
                ).fetchone(),
            )
    except sqlite3.Error:
        return False
    return row == ("9",)


def save_historical_market_summary(
    path: Path,
    *,
    source: str,
    summary: HistoricalMarketSummaryRow,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO historical_market_daily_summary (
                source,
                actual_data_date,
                benchmark_close,
                turnover_cny,
                security_count,
                advancers,
                decliners,
                unchanged
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, actual_data_date) DO UPDATE SET
                benchmark_close = excluded.benchmark_close,
                turnover_cny = excluded.turnover_cny,
                security_count = excluded.security_count,
                advancers = excluded.advancers,
                decliners = excluded.decliners,
                unchanged = excluded.unchanged
            """,
            (
                source,
                summary.actual_data_date.isoformat(),
                summary.benchmark_close,
                summary.turnover_cny,
                summary.security_count,
                summary.advancers,
                summary.decliners,
                summary.unchanged,
            ),
        )


def save_historical_benchmark_fact(
    path: Path,
    *,
    source: str,
    fact: HistoricalBenchmarkFactRow,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO historical_benchmark_facts (
                source,
                actual_data_date,
                name,
                open,
                high,
                low,
                close,
                volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, actual_data_date) DO UPDATE SET
                name = excluded.name,
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            (
                source,
                fact.actual_data_date.isoformat(),
                fact.name,
                fact.open,
                fact.high,
                fact.low,
                fact.close,
                fact.volume,
            ),
        )


def save_history_symbol_batch(
    path: Path,
    *,
    source: str,
    range_start: date,
    range_end: date,
    code: str,
    facts: list[HistoricalSecurityFactRow],
    completed_at: datetime,
) -> None:
    with sqlite3.connect(path) as connection:
        with connection:
            connection.executemany(
                """
                INSERT INTO historical_security_facts (
                    source,
                    actual_data_date,
                    code,
                    name,
                    open,
                    high,
                    low,
                    close,
                    previous_close,
                    change_pct,
                    volume,
                    turnover_cny,
                    listing_trading_days
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, actual_data_date, code) DO UPDATE SET
                    name = excluded.name,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    previous_close = excluded.previous_close,
                    change_pct = excluded.change_pct,
                    volume = excluded.volume,
                    turnover_cny = excluded.turnover_cny,
                    listing_trading_days = excluded.listing_trading_days
                """,
                [
                    (
                        source,
                        fact.actual_data_date.isoformat(),
                        fact.code,
                        fact.name,
                        fact.open,
                        fact.high,
                        fact.low,
                        fact.close,
                        fact.previous_close,
                        fact.change_pct,
                        fact.volume,
                        fact.turnover_cny,
                        fact.listing_trading_days,
                    )
                    for fact in facts
                ],
            )
            connection.execute(
                """
                INSERT INTO history_ingestion_progress (
                    source,
                    range_start,
                    range_end,
                    code,
                    row_count,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, range_start, range_end, code) DO UPDATE SET
                    row_count = excluded.row_count,
                    completed_at = excluded.completed_at
                """,
                (
                    source,
                    range_start.isoformat(),
                    range_end.isoformat(),
                    code,
                    len(facts),
                    completed_at.isoformat(),
                ),
            )


def completed_history_symbols(
    path: Path,
    *,
    source: str,
    range_start: date,
    range_end: date,
) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT code
            FROM history_ingestion_progress
            WHERE source = ? AND range_start = ? AND range_end = ?
            """,
            (source, range_start.isoformat(), range_end.isoformat()),
        ).fetchall()
    return {cast(str, row[0]) for row in rows}


def historical_security_facts_for_date(
    path: Path,
    *,
    source: str,
    actual_data_date: date,
) -> list[HistoricalSecurityFactRow]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, code, name, open, high, low, close,
                   previous_close, change_pct, volume, turnover_cny,
                   listing_trading_days
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            ORDER BY code
            """,
            (source, actual_data_date.isoformat()),
        ).fetchall()
    return [
        HistoricalSecurityFactRow(
            actual_data_date=date.fromisoformat(cast(str, row[0])),
            code=cast(str, row[1]),
            name=cast(str, row[2]),
            open=cast(float, row[3]),
            high=cast(float, row[4]),
            low=cast(float, row[5]),
            close=cast(float, row[6]),
            previous_close=cast(float, row[7]),
            change_pct=cast(float, row[8]),
            volume=cast(int, row[9]),
            turnover_cny=cast(int, row[10]),
            listing_trading_days=cast(int, row[11]),
        )
        for row in rows
    ]


def save_market_facts(
    path: Path,
    *,
    actual_data_date: date,
    source: str,
    payload_json: str,
    collected_at: datetime,
) -> int:
    content_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO market_fact_snapshots (
                actual_data_date,
                source,
                payload_json,
                collected_at,
                content_sha256
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                actual_data_date.isoformat(),
                source,
                payload_json,
                collected_at.isoformat(),
                content_sha256,
            ),
        )
        if cursor.rowcount == 1:
            return cast(int, cursor.lastrowid)
        row = cast(
            tuple[int],
            connection.execute(
                """
                SELECT id
                FROM market_fact_snapshots
                WHERE actual_data_date = ? AND content_sha256 = ?
                """,
                (actual_data_date.isoformat(), content_sha256),
            ).fetchone(),
        )
    return row[0]


def latest_market_facts(
    path: Path, actual_data_date: date
) -> MarketFactsRow | None:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[int, str, str, str, str] | None,
            connection.execute(
                """
                SELECT id, actual_data_date, source, payload_json, collected_at
                FROM market_fact_snapshots
                WHERE actual_data_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (actual_data_date.isoformat(),),
            ).fetchone(),
        )
    if row is None:
        return None
    return MarketFactsRow(
        id=row[0],
        actual_data_date=date.fromisoformat(row[1]),
        source=row[2],
        payload_json=row[3],
        collected_at=datetime.fromisoformat(row[4]),
    )


def latest_snapshot(path: Path, target_date: date) -> SnapshotRow | None:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[str, str, str] | None,
            connection.execute(
                """
                SELECT target_date, payload_json, published_at
                FROM daily_snapshots
                WHERE target_date <= ?
                ORDER BY target_date DESC
                LIMIT 1
                """,
                (target_date.isoformat(),),
            ).fetchone(),
        )
    if row is None:
        return None
    actual_data_date, payload_json, published_at = row
    return SnapshotRow(
        actual_data_date=date.fromisoformat(actual_data_date),
        payload_json=payload_json,
        published_at=datetime.fromisoformat(published_at),
    )


def latest_task_status(path: Path, target_date: date) -> str:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[str] | None,
            connection.execute(
                """
                SELECT status
                FROM task_runs
                WHERE target_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_date.isoformat(),),
            ).fetchone(),
        )
    return row[0] if row else "not_run"


def snapshot_exists(path: Path, target_date: date) -> bool:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT 1 FROM daily_snapshots WHERE target_date = ?",
            (target_date.isoformat(),),
        ).fetchone()
    return row is not None


def create_task_run(
    path: Path,
    target_date: date,
    started_at: datetime,
    *,
    trigger_method: str = "manual",
) -> int:
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO task_runs (
                trigger_method,
                target_date,
                started_at,
                status,
                stage
            )
            VALUES (?, ?, ?, 'running', 'queued')
            """,
            (trigger_method, target_date.isoformat(), started_at.isoformat()),
        )
        return cast(int, cursor.lastrowid)


def publish_snapshot(
    path: Path,
    *,
    task_id: int,
    target_date: date,
    payload_json: str,
    published_at: datetime,
    simulate_failure: bool = False,
) -> None:
    with sqlite3.connect(path) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO daily_snapshots (
                    target_date,
                    payload_json,
                    published_at
                )
                VALUES (?, ?, ?)
                ON CONFLICT(target_date) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    published_at = excluded.published_at
                """,
                (target_date.isoformat(), payload_json, published_at.isoformat()),
            )
            if simulate_failure:
                raise RuntimeError("模拟事务发布失败")
            connection.execute(
                """
                UPDATE task_runs
                SET finished_at = ?, status = 'succeeded', stage = 'completed',
                    error_summary = NULL
                WHERE id = ?
                """,
                (published_at.isoformat(), task_id),
            )


def update_task_stage(path: Path, task_id: int, stage: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE task_runs SET stage = ? WHERE id = ?",
            (stage, task_id),
        )


def fail_task_run(
    path: Path,
    task_id: int,
    finished_at: datetime,
    *,
    stage: str,
    error_summary: str,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE task_runs
            SET finished_at = ?, status = 'failed', stage = ?, error_summary = ?
            WHERE id = ?
            """,
            (finished_at.isoformat(), stage, error_summary, task_id),
        )


def task_runs(path: Path, *, limit: int = 20) -> list[TaskRunRow]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT id, trigger_method, target_date, started_at, finished_at,
                   stage, status, error_summary
            FROM task_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        TaskRunRow(
            id=cast(int, row[0]),
            trigger_method=cast(str, row[1]),
            target_date=date.fromisoformat(cast(str, row[2])),
            started_at=datetime.fromisoformat(cast(str, row[3])),
            finished_at=(
                datetime.fromisoformat(cast(str, row[4])) if row[4] else None
            ),
            stage=cast(str, row[5]),
            status=cast(str, row[6]),
            error_summary=cast(str | None, row[7]),
        )
        for row in rows
    ]


def latest_task_run(path: Path, target_date: date) -> TaskRunRow | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT id, trigger_method, target_date, started_at, finished_at,
                   stage, status, error_summary
            FROM task_runs
            WHERE target_date = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return TaskRunRow(
        id=cast(int, row[0]),
        trigger_method=cast(str, row[1]),
        target_date=date.fromisoformat(cast(str, row[2])),
        started_at=datetime.fromisoformat(cast(str, row[3])),
        finished_at=datetime.fromisoformat(cast(str, row[4])) if row[4] else None,
        stage=cast(str, row[5]),
        status=cast(str, row[6]),
        error_summary=cast(str | None, row[7]),
    )


def claim_automation_date(
    path: Path,
    target_date: date,
    claimed_at: datetime,
    *,
    stale_after_seconds: int = 7_200,
) -> str | None:
    stale_before = claimed_at.timestamp() - stale_after_seconds
    claim_id = str(uuid.uuid4())
    with sqlite3.connect(path) as connection:
        existing = connection.execute(
            "SELECT claimed_at, claim_id FROM automation_claims WHERE target_date = ?",
            (target_date.isoformat(),),
        ).fetchone()
        if existing is not None:
            existing_claim = cast(str, existing[0])
            existing_claim_id = cast(str, existing[1])
            existing_time = datetime.fromisoformat(existing_claim)
            if existing_time.timestamp() < stale_before:
                connection.execute(
                    "DELETE FROM automation_claims WHERE target_date = ? AND claim_id = ?",
                    (target_date.isoformat(), existing_claim_id),
                )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO automation_claims (target_date, claimed_at, claim_id) VALUES (?, ?, ?)",
            (target_date.isoformat(), claimed_at.isoformat(), claim_id),
        )
        return claim_id if cursor.rowcount == 1 else None


def release_automation_date(path: Path, target_date: date, claim_id: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM automation_claims WHERE target_date = ? AND claim_id = ?",
            (target_date.isoformat(), claim_id),
        )


def scheduled_attempt_exists(path: Path, target_date: date) -> bool:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM task_runs
            WHERE target_date = ? AND trigger_method = 'scheduled'
            LIMIT 1
            """,
            (target_date.isoformat(),),
        ).fetchone()
    return row is not None


def scheduled_attempt_count(path: Path, target_date: date) -> int:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[int],
            connection.execute(
                """
                SELECT COUNT(*) FROM task_runs
                WHERE target_date = ? AND trigger_method = 'scheduled'
                """,
                (target_date.isoformat(),),
            ).fetchone(),
        )
    return row[0]
