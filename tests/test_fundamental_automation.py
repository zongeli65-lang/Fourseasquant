from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

import fourseasquant.fundamental_automation as automation_module
from fourseasquant.database import initialize_database
from fourseasquant.fundamental_automation import (
    FundamentalUpdateLease,
    run_scheduled_fundamental_update,
)
from fourseasquant.fundamental_capital_actions import CapitalActionSnapshot
from fourseasquant.fundamental_repository import (
    claim_fundamental_update,
    read_latest_capital_action_batch,
    release_fundamental_update,
    save_capital_action_snapshot,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_fundamental_schedule_waits_until_configured_time(
    tmp_path: Path,
) -> None:
    database = tmp_path / "early.db"
    initialize_database(database)
    calls: list[list[str]] = []

    def record_command(command: list[str], _path: Path) -> int:
        calls.append(command)
        return 0

    outcome = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 29, tzinfo=BEIJING),
        path=database,
        command_runner=record_command,
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "尚未到自动更新时间"
    assert calls == []


def test_fundamental_schedule_skips_when_another_process_owns_date(
    tmp_path: Path,
) -> None:
    database = tmp_path / "claimed.db"
    initialize_database(database)
    target = date(2026, 7, 24)
    claimed_at = datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING)
    claim_id = claim_fundamental_update(database, target, claimed_at)
    assert claim_id is not None

    outcome = run_scheduled_fundamental_update(
        now=claimed_at,
        path=database,
        command_runner=lambda _command, _path: 0,
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "该交易日基本面任务已在运行"
    release_fundamental_update(database, target, claim_id)


def test_long_running_fundamental_claim_is_renewed_before_expiry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fundamental-claim-heartbeat.db"
    initialize_database(database)
    target = date(2026, 7, 24)
    started = datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING)
    lease = FundamentalUpdateLease.acquire(
        database,
        target,
        claimed_at=started,
        heartbeat_seconds=0.01,
        now=lambda: started.replace(hour=18, minute=29, second=59),
    )
    assert lease is not None

    with lease:
        sleep(0.05)
        competing = claim_fundamental_update(
            database,
            target,
            started.replace(hour=18, minute=30, second=1),
        )

    assert competing is None


def test_failed_capital_command_records_visible_failure_and_waits_for_retry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "failed.db"
    initialize_database(database)

    failed = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )
    waiting = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 31, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 0,
    )
    batch = read_latest_capital_action_batch(
        database,
        as_of_date=date(2026, 7, 24),
    )

    assert failed.status == "failed"
    assert failed.stage == "capital_actions"
    assert failed.notification_due is False
    assert waiting.status == "skipped"
    assert waiting.reason == "等待下一次自动重试"
    assert batch is not None
    assert batch.status == "failed"
    assert batch.errors == {"__global__": "资本行为命令退出码 2"}


def test_failed_capital_retries_every_thirty_minutes_through_2130(
    tmp_path: Path,
) -> None:
    database = tmp_path / "extended-capital-retry-window.db"
    initialize_database(database)

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
        outcome = run_scheduled_fundamental_update(
            now=datetime(2026, 7, 24, hour, minute, tzinfo=BEIJING),
            path=database,
            command_runner=lambda _command, _path: 2,
        )
        assert outcome.status == "failed"
        assert outcome.notification_due == ((hour, minute) == (21, 30))

    after_window = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 21, 31, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert after_window.status == "skipped"
    assert after_window.reason == "自动重试已用尽，请在网站内手动重试"


def test_long_capital_failure_after_final_slot_is_notification_due(
    tmp_path: Path,
) -> None:
    database = tmp_path / "long-capital-failure.db"
    initialize_database(database)

    outcome = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 21, 0, tzinfo=BEIJING),
        now_provider=lambda: datetime(
            2026,
            7,
            24,
            21,
            31,
            tzinfo=BEIJING,
        ),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert outcome.status == "failed"
    assert outcome.notification_due is True


def test_long_month_end_failure_after_final_slot_is_notification_due(
    tmp_path: Path,
) -> None:
    database = tmp_path / "long-month-end-failure.db"
    initialize_database(database)
    target_date = date(2026, 7, 31)
    save_capital_action_snapshot(
        database,
        CapitalActionSnapshot(
            as_of_date=target_date,
            expected_codes=["600000"],
            completed_codes=["600000"],
            events=[],
            errors={},
        ),
        collected_at=datetime(2026, 7, 31, 20, 59, tzinfo=BEIJING),
    )

    outcome = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 31, 21, 0, tzinfo=BEIJING),
        now_provider=lambda: datetime(
            2026,
            7,
            31,
            21,
            31,
            tzinfo=BEIJING,
        ),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert outcome.status == "failed"
    assert outcome.stage == "monthly_snapshot"
    assert outcome.notification_due is True


def test_capital_schedule_does_not_backfill_missed_retry_slots(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missed-capital-retry-slot.db"
    initialize_database(database)

    first = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )
    between_slots = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 18, 41, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )
    after_window = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 21, 31, tzinfo=BEIJING),
        path=tmp_path / "fresh-after-window.db",
        command_runner=lambda _command, _path: 2,
    )

    assert first.status == "failed"
    assert between_slots.status == "skipped"
    assert between_slots.reason == "等待下一次自动重试"
    assert after_window.status == "skipped"
    assert after_window.reason == "自动重试已用尽，请在网站内手动重试"


def test_each_capital_retry_slot_starts_at_most_once(
    tmp_path: Path,
) -> None:
    database = tmp_path / "one-capital-attempt-per-slot.db"
    initialize_database(database)

    first = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )
    later_slot = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 17, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )
    duplicate = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 17, 30, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert first.status == "failed"
    assert later_slot.status == "failed"
    assert duplicate.status == "skipped"
    assert duplicate.reason == "等待下一次自动重试"


def test_manual_capital_failure_does_not_consume_a_scheduled_slot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-capital-slot-isolation.db"
    initialize_database(database)

    manual = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING),
        path=database,
        force=True,
        command_runner=lambda _command, _path: 2,
    )
    scheduled = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 16, 30, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert manual.status == "failed"
    assert manual.notification_due is True
    assert scheduled.status == "failed"
    assert scheduled.notification_due is False


def test_continuation_failure_does_not_consume_a_scheduled_slot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "continuation-capital-slot-isolation.db"
    initialize_database(database)

    continuation = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 17, 0, tzinfo=BEIJING),
        path=database,
        ignore_schedule=True,
        command_runner=lambda _command, _path: 2,
    )
    scheduled = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 17, 0, 30, tzinfo=BEIJING),
        path=database,
        command_runner=lambda _command, _path: 2,
    )

    assert continuation.status == "failed"
    assert continuation.notification_due is False
    assert scheduled.status == "failed"
    assert scheduled.notification_due is False


def test_startup_catchup_failure_notifies_immediately(
    tmp_path: Path,
) -> None:
    database = tmp_path / "startup-capital-notification.db"
    initialize_database(database)

    outcome = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 24, 17, 0, tzinfo=BEIJING),
        path=database,
        ignore_schedule=True,
        notify_failure_immediately=True,
        command_runner=lambda _command, _path: 2,
    )

    assert outcome.status == "failed"
    assert outcome.notification_due is True


def test_month_end_runs_capital_then_monthly_but_other_days_only_capital(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    normal_database = tmp_path / "normal.db"
    month_end_database = tmp_path / "month-end.db"
    initialize_database(normal_database)
    initialize_database(month_end_database)
    monthly_complete_paths: set[Path] = set()
    monkeypatch.setattr(
        automation_module,
        "read_monthly_snapshot_coverage",
        lambda path, **_kwargs: (
            {"600000"} if path in monthly_complete_paths else set()
        ),
    )

    def run_successfully(command: list[str], path: Path) -> int:
        if command[-3].endswith("import_fundamental_capital_actions.py"):
            target_date = date.fromisoformat(command[-1])
            save_capital_action_snapshot(
                path,
                CapitalActionSnapshot(
                    as_of_date=target_date,
                    expected_codes=["600000"],
                    completed_codes=["600000"],
                    events=[],
                    errors={},
                ),
                collected_at=datetime.combine(
                    target_date,
                    datetime.min.time(),
                    tzinfo=BEIJING,
                ),
            )
        if command[-3].endswith("import_fundamental_monthly_akshare.py"):
            monthly_complete_paths.add(path)
        calls_by_path.setdefault(path, []).append(command)
        return 0

    calls_by_path: dict[Path, list[list[str]]] = {}
    normal = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 30, 16, 30, tzinfo=BEIJING),
        path=normal_database,
        command_runner=run_successfully,
    )
    month_end = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 31, 16, 30, tzinfo=BEIJING),
        path=month_end_database,
        command_runner=run_successfully,
    )

    assert normal.status == "succeeded"
    assert len(calls_by_path[normal_database]) == 1
    assert month_end.status == "succeeded"
    assert [
        Path(command[1]).name
        for command in calls_by_path[month_end_database]
    ] == [
        "import_fundamental_capital_actions.py",
        "import_fundamental_monthly_akshare.py",
    ]


def test_failed_month_end_snapshot_retries_without_rerunning_capital(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "month-end-retry.db"
    initialize_database(database)
    target_date = date(2026, 7, 31)
    save_capital_action_snapshot(
        database,
        CapitalActionSnapshot(
            as_of_date=target_date,
            expected_codes=["600000"],
            completed_codes=["600000"],
            events=[],
            errors={},
        ),
        collected_at=datetime(2026, 7, 31, 16, 30, tzinfo=BEIJING),
    )
    monthly_complete = False
    calls: list[str] = []

    def coverage(_path: Path, **_kwargs: object) -> set[str]:
        return {"600000"} if monthly_complete else set()

    def run_monthly(command: list[str], _path: Path) -> int:
        nonlocal monthly_complete
        calls.append(Path(command[1]).name)
        if len(calls) == 1:
            return 1
        monthly_complete = True
        return 0

    monkeypatch.setattr(
        automation_module,
        "read_monthly_snapshot_coverage",
        coverage,
    )
    failed = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 31, 16, 30, tzinfo=BEIJING),
        path=database,
        command_runner=run_monthly,
    )
    waiting = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 31, 16, 31, tzinfo=BEIJING),
        path=database,
        command_runner=run_monthly,
    )
    retried = run_scheduled_fundamental_update(
        now=datetime(2026, 7, 31, 17, 0, tzinfo=BEIJING),
        path=database,
        command_runner=run_monthly,
    )

    assert failed.stage == "monthly_snapshot"
    assert failed.status == "failed"
    assert waiting.status == "skipped"
    assert retried.status == "succeeded"
    assert calls == [
        "import_fundamental_monthly_akshare.py",
        "import_fundamental_monthly_akshare.py",
    ]
