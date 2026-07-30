from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from threading import Barrier, Thread
from time import sleep
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

import fourseasquant.automation as automation_module

from fourseasquant.automation import (
    AutomationOutcome,
    RecordingNotifier,
    TargetDateClaimLease,
    run_scheduled_task,
    run_startup_catchup,
)
from fourseasquant.daily_snapshots import TaskRunResponse, TaskTrigger, execute_daily_task
from fourseasquant.database import (
    claim_automation_date,
    initialize_database,
    latest_snapshot,
    task_runs,
)
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


def test_production_schedule_retries_when_snapshot_exists_but_candles_are_stale(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "stale-candles.db"
    initialize_database(database)
    target_date = datetime(2026, 7, 21, tzinfo=BEIJING).date()
    execute_daily_task(target_date, path=database)
    real_candle_runs: list[Path] = []

    def run_with_candles(
        run_date: date,
        *,
        path: Path,
        trigger_method: TaskTrigger,
        started_at: datetime,
    ) -> TaskRunResponse:
        real_candle_runs.append(path)
        return execute_daily_task(
            run_date,
            path=path,
            trigger_method=trigger_method,
            started_at=started_at,
        )

    monkeypatch.setattr(automation_module, "database_path", lambda: database)
    monkeypatch.setattr(
        automation_module,
        "execute_daily_task_with_candles",
        run_with_candles,
    )

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        notifier=RecordingNotifier(),
    )

    assert outcome.status == "succeeded"
    assert real_candle_runs == [database]


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


def test_failed_schedule_retries_at_thirty_minute_offsets(tmp_path: Path) -> None:
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
        now=datetime(2026, 7, 21, 16, 59, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )
    second = run_scheduled_task(
        now=datetime(2026, 7, 21, 17, 0, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )

    assert first.status == "failed"
    assert waiting.status == "skipped"
    assert second.status == "failed"
    assert len(task_runs(database)) == 2


def test_failed_schedule_retries_every_thirty_minutes_through_2130(
    tmp_path: Path,
) -> None:
    database = tmp_path / "extended-retry-window.db"
    initialize_database(database)
    notifier = RecordingNotifier()

    for hour, minute in (
        (16, 30),
        (17, 0),
        (17, 30),
        (18, 0),
        (18, 30),
        (19, 0),
        (19, 30),
        (20, 0),
        (20, 30),
        (21, 0),
        (21, 30),
    ):
        outcome = run_scheduled_task(
            now=datetime(2026, 7, 21, hour, minute, tzinfo=BEIJING),
            path=database,
            notifier=notifier,
            simulate_failure_stage="market_prepare",
        )
        assert outcome.status == "failed"

    after_window = run_scheduled_task(
        now=datetime(2026, 7, 21, 21, 31, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
        simulate_failure_stage="market_prepare",
    )

    assert after_window.status == "skipped"
    assert after_window.reason == "自动重试已用尽，请在网站内手动重试"
    assert len(task_runs(database)) == 11


def test_long_market_failure_after_final_slot_notifies_immediately(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "long-market-failure.db"
    initialize_database(database)
    notifier = RecordingNotifier()
    target_date = date(2026, 7, 21)

    monkeypatch.setattr(
        automation_module,
        "_execute_claimed_task",
        lambda **_kwargs: TaskRunResponse(
            id=1,
            trigger_method="scheduled",
            target_date=target_date,
            started_at=datetime(
                2026,
                7,
                21,
                21,
                0,
                tzinfo=BEIJING,
            ),
            finished_at=datetime(
                2026,
                7,
                21,
                21,
                31,
                tzinfo=BEIJING,
            ),
            stage="market_prepare",
            stage_label="市场准备",
            status="failed",
            error_summary="上游超时",
        ),
    )

    outcome = run_scheduled_task(
        now=datetime(2026, 7, 21, 21, 0, tzinfo=BEIJING),
        path=database,
        notifier=notifier,
    )

    assert outcome.status == "failed"
    assert outcome.notification_status == "sent"
    assert len(notifier.events) == 1


def test_schedule_does_not_backfill_missed_slots_after_2130(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missed-retry-window.db"
    initialize_database(database)

    after_window = run_scheduled_task(
        now=datetime(2026, 7, 21, 21, 31, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert after_window.status == "skipped"
    assert after_window.task is None
    assert after_window.reason == "自动重试已用尽，请在网站内手动重试"
    assert task_runs(database) == []


def test_schedule_does_not_backfill_a_missed_slot_between_half_hours(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missed-mid-window-slot.db"
    initialize_database(database)

    first = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )
    between_slots = run_scheduled_task(
        now=datetime(2026, 7, 21, 18, 41, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert first.status == "failed"
    assert between_slots.status == "skipped"
    assert between_slots.task is None
    assert between_slots.reason == "等待下一次自动重试"
    assert len(task_runs(database)) == 1


def test_each_retry_slot_starts_at_most_once_when_an_earlier_slot_was_missed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "one-attempt-per-slot.db"
    initialize_database(database)

    first = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )
    later_slot = run_scheduled_task(
        now=datetime(2026, 7, 21, 17, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )
    duplicate = run_scheduled_task(
        now=datetime(2026, 7, 21, 17, 30, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert first.status == "failed"
    assert later_slot.status == "failed"
    assert duplicate.status == "skipped"
    assert duplicate.task is None
    assert duplicate.reason == "等待下一次自动重试"
    assert len(task_runs(database)) == 2


def test_manual_market_failure_does_not_consume_a_scheduled_slot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-market-slot-isolation.db"
    initialize_database(database)

    manual = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        force=True,
        simulate_failure_stage="market_prepare",
    )
    scheduled = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert manual.status == "failed"
    assert manual.task is not None
    assert manual.task.trigger_method == "manual"
    assert scheduled.status == "failed"
    assert scheduled.task is not None
    assert scheduled.task.trigger_method == "scheduled"


def test_market_slot_is_rechecked_inside_the_date_lease(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "market-slot-race.db"
    initialize_database(database)
    monkeypatch.setattr(
        automation_module,
        "market_schedule_slot_exists",
        lambda *_args, **_kwargs: False,
    )

    first = run_scheduled_task(
        now=datetime(2026, 7, 21, 17, 0, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )
    raced = run_scheduled_task(
        now=datetime(2026, 7, 21, 17, 0, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert first.status == "failed"
    assert raced.status == "skipped"
    assert raced.task is None
    assert len(task_runs(database)) == 1


def test_published_market_remains_available_to_downstream_after_retry_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "published-after-window.db"
    initialize_database(database)

    published = run_scheduled_task(
        now=datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
    )
    after_window = run_scheduled_task(
        now=datetime(2026, 7, 21, 21, 31, tzinfo=BEIJING),
        path=database,
        notifier=RecordingNotifier(),
    )

    assert published.status == "succeeded"
    assert after_window.status == "skipped"
    assert after_window.reason == "该交易日已发布"


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


def test_startup_retries_latest_failed_trading_day_with_real_candles(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "failed-latest-catchup.db"
    initialize_database(database)
    failed_date = date(2026, 7, 27)
    execute_daily_task(
        failed_date,
        path=database,
        trigger_method="scheduled",
        simulate_failure_stage="market_prepare",
    )
    candle_runs: list[date] = []

    def run_with_candles(
        run_date: date,
        *,
        path: Path,
        trigger_method: TaskTrigger,
        started_at: datetime,
    ) -> TaskRunResponse:
        candle_runs.append(run_date)
        return execute_daily_task(
            run_date,
            path=path,
            trigger_method=trigger_method,
            started_at=started_at,
        )

    monkeypatch.setattr(automation_module, "database_path", lambda: database)
    monkeypatch.setattr(
        automation_module,
        "execute_daily_task_with_candles",
        run_with_candles,
    )

    outcome = run_startup_catchup(
        now=datetime(2026, 7, 28, 9, 0, tzinfo=BEIJING),
        notifier=RecordingNotifier(),
    )

    assert outcome.status == "succeeded"
    assert outcome.target_date == failed_date
    assert candle_runs == [failed_date]


def test_startup_catchup_does_not_consume_a_scheduled_retry_slot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "startup-retry-slots.db"
    initialize_database(database)

    def fail_market_stage(
        run_date: date,
        *,
        path: Path,
        trigger_method: TaskTrigger,
        simulate_failure_stage: str | None = None,
        propagate_unexpected: bool = True,
        started_at: datetime | None = None,
    ) -> TaskRunResponse:
        del simulate_failure_stage, propagate_unexpected
        return execute_daily_task(
            run_date,
            path=path,
            trigger_method=trigger_method,
            simulate_failure_stage="market_prepare",
            started_at=started_at,
        )

    monkeypatch.setattr(automation_module, "execute_daily_task", fail_market_stage)
    current = datetime(2026, 7, 21, 16, 30, tzinfo=BEIJING)

    startup = run_startup_catchup(
        now=current,
        path=database,
        notifier=RecordingNotifier(),
    )
    scheduled = run_scheduled_task(
        now=current,
        path=database,
        notifier=RecordingNotifier(),
        simulate_failure_stage="market_prepare",
    )

    assert startup.status == "failed"
    assert startup.task is not None
    assert startup.task.trigger_method == "startup_catchup"
    assert scheduled.status == "failed"
    assert [run.trigger_method for run in task_runs(database)] == [
        "scheduled",
        "startup_catchup",
    ]


def test_manual_today_before_close_targets_previous_trading_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-target.db"
    initialize_database(database)

    before_close = automation_module.resolve_manual_target_date(
        date(2026, 7, 28),
        now=datetime(2026, 7, 28, 15, 0, tzinfo=BEIJING),
        path=database,
    )
    after_close = automation_module.resolve_manual_target_date(
        date(2026, 7, 28),
        now=datetime(2026, 7, 28, 16, 30, tzinfo=BEIJING),
        path=database,
    )
    selected_history = automation_module.resolve_manual_target_date(
        date(2026, 7, 24),
        now=datetime(2026, 7, 28, 15, 0, tzinfo=BEIJING),
        path=database,
    )

    assert before_close == date(2026, 7, 27)
    assert after_close == date(2026, 7, 28)
    assert selected_history == date(2026, 7, 24)


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


def test_long_running_target_date_claim_is_renewed_before_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "claim-heartbeat.db"
    initialize_database(database)
    target = date(2026, 7, 24)
    started = datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING)
    lease = TargetDateClaimLease.acquire(
        database,
        target,
        claimed_at=started,
        heartbeat_seconds=0.01,
        now=lambda: started.replace(hour=18, minute=29, second=59),
    )
    assert lease is not None

    with lease:
        sleep(0.05)
        competing = claim_automation_date(
            database,
            target,
            started.replace(hour=18, minute=30, second=1),
        )

    assert competing is None
