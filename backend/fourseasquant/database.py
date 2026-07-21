from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class SnapshotRow:
    actual_data_date: date
    payload_json: str
    published_at: datetime


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
                status TEXT NOT NULL
            )
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
            ) VALUES (1, '16:30', '沪深 300', 'simulation', 20)
            """
        )
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES ('schema_version', '1')
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
    return row == ("1",)


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


def create_task_run(path: Path, target_date: date, started_at: datetime) -> int:
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO task_runs (
                trigger_method,
                target_date,
                started_at,
                status
            )
            VALUES (?, ?, ?, 'running')
            """,
            ("manual", target_date.isoformat(), started_at.isoformat()),
        )
        return cast(int, cursor.lastrowid)


def publish_snapshot(
    path: Path,
    *,
    task_id: int,
    target_date: date,
    payload_json: str,
    published_at: datetime,
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
            connection.execute(
                """
                UPDATE task_runs
                SET finished_at = ?, status = 'succeeded'
                WHERE id = ?
                """,
                (published_at.isoformat(), task_id),
            )


def fail_task_run(path: Path, task_id: int, finished_at: datetime) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE task_runs
            SET finished_at = ?, status = 'failed'
            WHERE id = ?
            """,
            (finished_at.isoformat(), task_id),
        )
