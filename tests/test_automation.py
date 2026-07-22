from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Barrier, Thread
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

import fourseasquant.automation as automation_module

from fourseasquant.automation import (
    AutomationOutcome,
    RecordingNotifier,
    run_scheduled_task,
    run_startup_catchup,
)
from fourseasquant.daily_snapshots import execute_daily_task
from fourseasquant.database import initialize_database, latest_snapshot, task_runs
from fourseasquant.settings import SettingsUpdate, save_settings


BEIJING = ZoneInfo("Asia/Shanghai")


def test_scheduled_and_manual_runs_publish_the_same_snapshot(tmp_path: Path) -> None:
    manual_database = tmp_path / "manual.db"
    scheduled_database = tmp_path / "scheduled.db"
    initialize_database(manual_database)
    initialize_database(scheduled_database)
    execute_daily_task(datetime(2026, 7, 21, tzinfo=BEIJING).date(), path=manual_database)
    notifier = RecordingNotifier()

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=scheduled_database,
        notifier=notifier,
    )

    assert outcome.status == "succeeded"
    manual = latest_snapshot(manual_database, outcome.target_date)
    scheduled = latest_snapshot(scheduled_database, outcome.target_date)
    assert manual is not None and scheduled is not None
    assert manual.payload_json == scheduled.payload_json
    assert notifier.events[0].title == "Fourseasquant 更新成功"
    assert task_runs(scheduled_database)[0].trigger_method == "scheduled"


def test_automatic_entrypoint_initializes_a_first_run_database(tmp_path: Path) -> None:
    database = tmp_path / "new-data-directory" / "first-run.db"
    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
    )

    assert outcome.status == "succeeded"
    assert database.exists()


def test_ready_database_skips_schema_initialization_on_periodic_wakeup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "ready.db"
    initialize_database(database)
    initialization_calls = 0

    def track_initialization(_: Path) -> None:
        nonlocal initialization_calls
        initialization_calls += 1

    monkeypatch.setattr(automation_module, "initialize_database", track_initialization)
    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 29, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
    )

    assert outcome.status == "skipped"
    assert initialization_calls == 0


def test_non_trading_day_is_skipped_without_creating_a_task(tmp_path: Path) -> None:
    database = tmp_path / "weekend.db"
    initialize_database(database)
    notifier = RecordingNotifier()

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 19, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
    )

    assert outcome.status == "skipped"
    assert task_runs(database) == []
    assert notifier.events == []


def test_persisted_beijing_schedule_controls_periodic_wakeup(tmp_path: Path) -> None:
    database = tmp_path / "configured-time.db"
    initialize_database(database)
    save_settings(
        database,
        SettingsUpdate(
            auto_update_time="16:45",
            benchmark="沪深 300",
            data_adapter="simulation",
            new_stock_exclusion_days=20,
        ),
    )
    notifier = RecordingNotifier()

    early = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 44, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
    )
    due = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 45, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
    )

    assert early.status == "skipped"
    assert early.reason == "尚未到自动更新时间"
    assert due.status == "succeeded"


def test_failure_notification_includes_stage(tmp_path: Path) -> None:
    database = tmp_path / "failure.db"
    initialize_database(database)
    notifier = RecordingNotifier()

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="strategy_run",
    )

    assert outcome.status == "failed"
    assert notifier.events[0].title == "Fourseasquant 更新失败"
    assert "策略运行" in notifier.events[0].message

    repeated = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 31, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="strategy_run",
    )
    assert repeated.status == "skipped"
    assert "下一次自动重试" in repeated.reason
    assert len(task_runs(database)) == 1
    assert len(notifier.events) == 1


def test_failed_schedule_retries_at_configured_offsets(tmp_path: Path) -> None:
    database = tmp_path / "retry.db"
    initialize_database(database)
    notifier = RecordingNotifier()

    first = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )
    waiting = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 39, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )
    second = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 40, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )

    assert first.status == "failed"
    assert waiting.status == "skipped"
    assert second.status == "failed"
    assert len(task_runs(database)) == 2


def test_notification_failure_does_not_rollback_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "notification-failure.db"
    log_path = tmp_path / "notification.jsonl"
    monkeypatch.setenv("FOURSEASQUANT_LOG_PATH", str(log_path))
    initialize_database(database)

    class BrokenNotifier:
        def send(self, title: str, message: str) -> None:
            raise OSError(f"通知不可用：{title} {message}")

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=BrokenNotifier(),
    )

    assert outcome.status == "succeeded"
    assert latest_snapshot(database, outcome.target_date) is not None
    assert outcome.notification_status == "failed"
    assert '"event": "notification_failed"' in log_path.read_text()


def test_startup_walks_back_to_the_latest_actual_gap(tmp_path: Path) -> None:
    database = tmp_path / "catchup.db"
    initialize_database(database)
    notifier = RecordingNotifier()
    startup_time = datetime(2026, 7, 20, 9, 0, tzinfo=BEIJING)

    first = run_startup_catchup(now=startup_time, path=database, notifier=notifier)
    second = run_startup_catchup(now=startup_time, path=database, notifier=notifier)

    assert first.status == "succeeded"
    assert first.target_date.isoformat() == "2026-07-17"
    assert second.status == "succeeded"
    assert second.target_date.isoformat() == "2026-07-16"
    assert len(task_runs(database)) == 2


def test_concurrent_automatic_triggers_publish_and_notify_only_once(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.db"
    initialize_database(database)
    notifier = RecordingNotifier()
    barrier = Barrier(2)
    outcomes: list[AutomationOutcome] = []

    def trigger() -> None:
        barrier.wait(timeout=2)
        outcomes.append(
            run_scheduled_task(
                now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
                path=database,
                notifier=notifier,
            )
        )

    threads = [Thread(target=trigger), Thread(target=trigger)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcome.status for outcome in outcomes) == ["skipped", "succeeded"]
    assert len(task_runs(database)) == 1
    assert len(notifier.events) == 1
