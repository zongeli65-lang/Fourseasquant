from __future__ import annotations

import sqlite3
from pathlib import Path

from fourseasquant.database import initialize_database


def test_version_one_task_history_is_migrated_with_meaningful_stages(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version-one.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO app_metadata VALUES ('schema_version', '1')"
        )
        connection.execute(
            """
            CREATE TABLE task_runs (
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
            INSERT INTO task_runs (
                trigger_method, target_date, started_at, finished_at, status
            ) VALUES (
                'manual', '2026-07-20', '2026-07-20T16:30:00+08:00',
                '2026-07-20T16:31:00+08:00', 'succeeded'
            )
            """
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        stage = connection.execute("SELECT stage FROM task_runs").fetchone()
    assert version == ("2",)
    assert stage == ("completed",)
