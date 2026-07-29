from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from fourseasquant.daily_snapshots import (
    PreparedMarket,
    execute_daily_task,
    read_dashboard,
)
from fourseasquant.database import initialize_database, latest_snapshot, latest_task_run
from fourseasquant.settings import SettingsUpdate, save_settings


def invalid_snapshot(_: date, __: PreparedMarket) -> Mapping[str, object]:
    return {"source": "simulation", "label": "缺少种子字段"}


def broken_strategy(_: date, __: PreparedMarket) -> Mapping[str, object]:
    raise ValueError("策略实现异常")


def test_invalid_snapshot_marks_task_failed_without_publishing(tmp_path: Path) -> None:
    database = tmp_path / "failed-task.db"
    initialize_database(database)

    with pytest.raises(ValidationError):
        execute_daily_task(
            date(2026, 7, 21),
            path=database,
            strategy_stage_factory=invalid_snapshot,
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


def test_real_strategy_exception_is_recorded_in_strategy_stage(tmp_path: Path) -> None:
    database = tmp_path / "strategy-error.db"
    initialize_database(database)

    with pytest.raises(ValueError, match="策略实现异常"):
        execute_daily_task(
            date(2026, 7, 21),
            path=database,
            strategy_stage_factory=broken_strategy,
        )

    task = latest_task_run(database, date(2026, 7, 21))
    assert task is not None
    assert task.status == "failed"
    assert task.stage == "strategy_run"
    assert task.error_summary == "ValueError: 策略实现异常"


def test_logging_failure_never_changes_success_or_recorded_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "logging-failure.db"
    initialize_database(database)

    def broken_log(**_: object) -> None:
        raise OSError("日志目录不可写")

    monkeypatch.setattr("fourseasquant.daily_snapshots.log_task_event", broken_log)

    success = execute_daily_task(date(2026, 7, 20), path=database)
    failure = execute_daily_task(
        date(2026, 7, 21),
        path=database,
        simulate_failure_stage="market_prepare",
    )

    assert success.status == "succeeded"
    assert failure.status == "failed"
    assert latest_snapshot(database, date(2026, 7, 20)) is not None
    failed_task = latest_task_run(database, date(2026, 7, 21))
    assert failed_task is not None
    assert failed_task.status == "failed"


def test_today_view_surfaces_newer_failed_trading_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "today-fallback.db"
    initialize_database(database)
    execute_daily_task(date(2026, 7, 24), path=database)
    execute_daily_task(
        date(2026, 7, 27),
        path=database,
        trigger_method="scheduled",
        simulate_failure_stage="market_prepare",
    )

    dashboard = read_dashboard(date(2026, 7, 28), path=database)

    assert dashboard.actual_data_date == date(2026, 7, 24)
    assert dashboard.task_status == "failed"
    assert dashboard.failure is not None
    assert dashboard.failure.stage == "market_prepare"
