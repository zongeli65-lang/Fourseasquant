from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import (
    claim_automation_date,
    initialize_database,
    release_automation_date,
)


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
    assert version == ("3",)
    assert stage == ("completed",)


def test_version_two_automation_claims_gain_owned_claim_ids(tmp_path: Path) -> None:
    database = tmp_path / "version-two.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO app_metadata VALUES ('schema_version', '2')")
        connection.execute(
            """
            CREATE TABLE automation_claims (
                target_date TEXT PRIMARY KEY,
                claimed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO automation_claims VALUES ('2026-07-21', '2026-07-21T16:30:00+08:00')"
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
        claim = connection.execute(
            "SELECT target_date, claim_id FROM automation_claims"
        ).fetchone()
    assert version == ("3",)
    assert claim is not None
    assert claim[0] == "2026-07-21"
    assert claim[1]


def test_stale_owner_cannot_release_newer_automation_claim(tmp_path: Path) -> None:
    database = tmp_path / "claim-owner.db"
    initialize_database(database)
    target = date(2026, 7, 21)
    started = datetime(2026, 7, 21, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    first_claim = claim_automation_date(database, target, started)
    assert first_claim is not None
    second_claim = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_201),
    )
    assert second_claim is not None

    release_automation_date(database, target, first_claim)
    third_claim = claim_automation_date(
        database,
        target,
        started + timedelta(seconds=7_202),
    )
    assert third_claim is None

    release_automation_date(database, target, second_claim)
