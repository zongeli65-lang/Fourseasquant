from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from fourseasquant.daily_snapshots import execute_daily_task
from fourseasquant.database import initialize_database


def invalid_snapshot(_: date) -> Mapping[str, object]:
    return {"source": "simulation", "label": "缺少种子字段"}


def test_invalid_snapshot_marks_task_failed_without_publishing(tmp_path: Path) -> None:
    database = tmp_path / "failed-task.db"
    initialize_database(database)

    with pytest.raises(ValidationError):
        execute_daily_task(
            date(2026, 7, 21),
            path=database,
            snapshot_factory=invalid_snapshot,
        )

    with sqlite3.connect(database) as connection:
        task = connection.execute(
            """
            SELECT trigger_method, target_date, started_at, finished_at, status
            FROM task_runs
            """
        ).fetchone()
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM daily_snapshots"
        ).fetchone()

    assert task is not None
    assert task[0] == "manual"
    assert task[1] == "2026-07-21"
    assert task[2] is not None
    assert task[3] is not None
    assert task[4] == "failed"
    assert snapshot_count == (0,)
