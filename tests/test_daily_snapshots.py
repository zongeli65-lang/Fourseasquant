from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from fourseasquant.daily_snapshots import execute_daily_task
from fourseasquant.database import initialize_database, latest_snapshot
from fourseasquant.settings import SettingsUpdate, save_settings


def invalid_snapshot(_: date, __: Path) -> Mapping[str, object]:
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


def test_default_factory_reads_settings_from_the_selected_database(tmp_path: Path) -> None:
    database = tmp_path / "selected-settings.db"
    initialize_database(database)
    save_settings(
        database,
        SettingsUpdate(
            auto_update_time="16:45",
            benchmark="中证 500",
            data_adapter="simulation_conservative",
            new_stock_exclusion_days=15,
        ),
    )

    result = execute_daily_task(date(2026, 7, 21), path=database)

    assert result.status == "succeeded"
    published = latest_snapshot(database, date(2026, 7, 21))
    assert published is not None
    payload = json.loads(published.payload_json)
    assert payload["source"] == "simulation_conservative"
    assert payload["strategy_performance"]["benchmark_label"] == "中证 500"
