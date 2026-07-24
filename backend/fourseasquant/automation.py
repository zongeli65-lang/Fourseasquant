from __future__ import annotations

import argparse
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.candlesticks import latest_candle_publication
from fourseasquant.daily_snapshots import (
    FailureStage,
    TaskRunResponse,
    execute_daily_task,
)
from fourseasquant.candle_daily_task import execute_daily_task_with_candles
from fourseasquant.database import (
    claim_automation_date,
    database_is_ready,
    database_path,
    initialize_database,
    release_automation_date,
    renew_automation_date_claim,
    scheduled_attempt_count,
    scheduled_attempt_exists,
    snapshot_exists,
)
from fourseasquant.settings import read_settings
from fourseasquant.task_logging import log_task_event
from fourseasquant.trading_calendar import (
    CALENDAR_SUPPORTED_END,
    CALENDAR_SUPPORTED_START,
    is_trading_day,
)


BEIJING = ZoneInfo("Asia/Shanghai")
CATCHUP_LOOKBACK_DAYS = 45
RETRY_MINUTES = (0, 10, 30, 60)


class TargetDateClaimLease:
    def __init__(
        self,
        *,
        path: Path,
        target_date: date,
        claim_id: str,
        heartbeat_seconds: float,
        now: Callable[[], datetime],
    ) -> None:
        self._path = path
        self._target_date = target_date
        self._claim_id = claim_id
        self._heartbeat_seconds = heartbeat_seconds
        self._now = now
        self._stop = Event()
        self._thread = Thread(
            target=self._heartbeat_loop,
            name=f"target-date-claim-{target_date.isoformat()}",
            daemon=True,
        )

    @classmethod
    def acquire(
        cls,
        path: Path,
        target_date: date,
        *,
        claimed_at: datetime | None = None,
        heartbeat_seconds: float = 60.0,
        now: Callable[[], datetime] | None = None,
    ) -> TargetDateClaimLease | None:
        current = claimed_at or datetime.now(BEIJING)
        claim_id = claim_automation_date(path, target_date, current)
        if claim_id is None:
            return None
        return cls(
            path=path,
            target_date=target_date,
            claim_id=claim_id,
            heartbeat_seconds=heartbeat_seconds,
            now=now or (lambda: datetime.now(BEIJING)),
        )

    def __enter__(self) -> TargetDateClaimLease:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._heartbeat_seconds))
        release_automation_date(self._path, self._target_date, self._claim_id)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            try:
                renewed = renew_automation_date_claim(
                    self._path,
                    self._target_date,
                    self._claim_id,
                    self._now(),
                )
            except sqlite3.OperationalError:
                continue
            if not renewed:
                return


class NotificationEvent(BaseModel):
    title: str
    message: str


class Notifier(Protocol):
    def send(self, title: str, message: str) -> None: ...


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[NotificationEvent] = []

    def send(self, title: str, message: str) -> None:
        self.events.append(NotificationEvent(title=title, message=message))


class MacOSNotifier:
    def send(self, title: str, message: str) -> None:
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        subprocess.run(
            ["osascript", "-e", script, title, message],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )


class AutomationOutcome(BaseModel):
    status: Literal["skipped", "succeeded", "failed"]
    target_date: date
    reason: str
    task: TaskRunResponse | None = None
    notification_status: Literal["not_sent", "sent", "failed"] = "not_sent"


def run_scheduled_task(
    *,
    now: datetime | None = None,
    path: Path | None = None,
    notifier: Notifier | None = None,
    force: bool = False,
    simulate_failure_stage: FailureStage | None = None,
) -> AutomationOutcome:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    selected_path = path or database_path()
    use_real_candles = path is None
    if not database_is_ready(selected_path):
        initialize_database(selected_path)
    target_date = current.date()
    settings = read_settings(selected_path)
    scheduled_time = time.fromisoformat(settings.auto_update_time)
    if not _calendar_supports(target_date):
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="目标日期超出交易日历支持范围",
        )
    if not is_trading_day(target_date):
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="非交易日",
        )
    allowed_attempts = _allowed_scheduled_attempts(
        current=current,
        scheduled_time=scheduled_time,
    )
    if not force and allowed_attempts == 0:
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="尚未到自动更新时间",
        )
    if not force and _target_data_is_complete(
        selected_path,
        target_date,
        require_candles=use_real_candles,
    ):
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        )
    attempts = scheduled_attempt_count(selected_path, target_date)
    if not force and attempts >= allowed_attempts:
        final_retry_due = _final_retry_time(target_date, scheduled_time)
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason=(
                "自动重试已用尽，请在网站内手动重试"
                if current >= final_retry_due
                else "等待下一次自动重试"
            ),
        )
    task = _execute_claimed_task(
        target_date=target_date,
        current=current,
        path=selected_path,
        simulate_failure_stage=simulate_failure_stage,
        allow_existing=force,
        use_real_candles=use_real_candles,
    )
    if task is None:
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日任务已在运行或发布",
        )
    outcome = AutomationOutcome(
        status=task.status,
        target_date=target_date,
        reason="自动任务完成" if task.status == "succeeded" else "自动任务失败",
        task=task,
    )
    if (
        outcome.status == "failed"
        and not force
        and simulate_failure_stage is None
        and current < _final_retry_time(target_date, scheduled_time)
    ):
        return outcome
    return _notify(outcome, notifier or MacOSNotifier(), current)


def _allowed_scheduled_attempts(*, current: datetime, scheduled_time: time) -> int:
    base = datetime.combine(current.date(), scheduled_time, tzinfo=BEIJING)
    return sum(current >= base + timedelta(minutes=minutes) for minutes in RETRY_MINUTES)


def _final_retry_time(target_date: date, scheduled_time: time) -> datetime:
    base = datetime.combine(target_date, scheduled_time, tzinfo=BEIJING)
    return base + timedelta(minutes=RETRY_MINUTES[-1])


def run_startup_catchup(
    *,
    now: datetime | None = None,
    path: Path | None = None,
    notifier: Notifier | None = None,
) -> AutomationOutcome:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    selected_path = path or database_path()
    # 启动补跑可能针对较早的策略快照；K 线数据集已按最新完整交易日
    # 独立发布，不能为每个历史缺口重复抓取全市场一年数据。
    use_real_candles = False
    if not database_is_ready(selected_path):
        initialize_database(selected_path)
    settings = read_settings(selected_path)
    latest_due_date = _latest_due_trading_day(
        current,
        time.fromisoformat(settings.auto_update_time),
    )
    target_date = _latest_missing_trading_day(
        latest_due_date,
        selected_path,
    )
    if target_date is None:
        return AutomationOutcome(
            status="skipped",
            target_date=latest_due_date,
            reason=f"最近 {CATCHUP_LOOKBACK_DAYS} 日内没有缺失交易日",
        )
    task = _execute_claimed_task(
        target_date=target_date,
        current=current,
        path=selected_path,
        use_real_candles=use_real_candles,
    )
    if task is None:
        return AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日任务已在运行或发布",
        )
    outcome = AutomationOutcome(
        status=task.status,
        target_date=target_date,
        reason="启动补跑完成" if task.status == "succeeded" else "启动补跑失败",
        task=task,
    )
    return _notify(outcome, notifier or MacOSNotifier(), current)


def _latest_due_trading_day(current: datetime, scheduled_time: time) -> date:
    candidate = current.date()
    if (
        not _calendar_supports(candidate)
        or not is_trading_day(candidate)
        or current.time().replace(tzinfo=None) < scheduled_time
    ):
        candidate -= timedelta(days=1)
    while candidate >= CALENDAR_SUPPORTED_START and not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    if not _calendar_supports(candidate):
        raise ValueError("找不到交易日历支持范围内的待补跑日期")
    return candidate


def _latest_missing_trading_day(latest_due_date: date, path: Path) -> date | None:
    earliest = max(
        CALENDAR_SUPPORTED_START,
        latest_due_date - timedelta(days=CATCHUP_LOOKBACK_DAYS),
    )
    candidate = latest_due_date
    while candidate >= earliest:
        if (
            is_trading_day(candidate)
            and not snapshot_exists(path, candidate)
            and not scheduled_attempt_exists(path, candidate)
        ):
            return candidate
        candidate -= timedelta(days=1)
    return None


def _execute_claimed_task(
    *,
    target_date: date,
    current: datetime,
    path: Path,
    simulate_failure_stage: FailureStage | None = None,
    allow_existing: bool = False,
    use_real_candles: bool = False,
) -> TaskRunResponse | None:
    lease = TargetDateClaimLease.acquire(
        path,
        target_date,
        claimed_at=current,
    )
    if lease is None:
        return None
    with lease:
        if not allow_existing and _target_data_is_complete(
            path,
            target_date,
            require_candles=use_real_candles,
        ):
            return None
        if use_real_candles and simulate_failure_stage is None:
            return execute_daily_task_with_candles(
                target_date,
                path=path,
                trigger_method="scheduled",
            )
        return execute_daily_task(
            target_date,
            path=path,
            trigger_method="scheduled",
            simulate_failure_stage=simulate_failure_stage,
            propagate_unexpected=False,
        )


def _calendar_supports(candidate: date) -> bool:
    return CALENDAR_SUPPORTED_START <= candidate <= CALENDAR_SUPPORTED_END


def _target_data_is_complete(
    path: Path,
    target_date: date,
    *,
    require_candles: bool,
) -> bool:
    if not snapshot_exists(path, target_date):
        return False
    if not require_candles:
        return True
    return latest_candle_publication(path, target_date) == target_date


def _notify(
    outcome: AutomationOutcome,
    notifier: Notifier,
    occurred_at: datetime,
) -> AutomationOutcome:
    task = outcome.task
    if task is None:
        return outcome
    title = (
        "Fourseasquant 更新成功"
        if outcome.status == "succeeded"
        else "Fourseasquant 更新失败"
    )
    message = (
        f"{outcome.target_date.isoformat()} 日频快照已发布 · {task.stage_label}"
        if outcome.status == "succeeded"
        else f"{outcome.target_date.isoformat()} · {task.stage_label}"
    )
    try:
        notifier.send(title, message)
        outcome.notification_status = "sent"
    except Exception as error:
        outcome.notification_status = "failed"
        try:
            log_task_event(
                event="notification_failed",
                task_id=task.id,
                target_date=outcome.target_date,
                stage=task.stage,
                occurred_at=occurred_at,
                trigger_method=task.trigger_method,
                error_type=type(error).__name__,
            )
        except Exception:
            pass
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description="Fourseasquant 本机自动任务")
    parser.add_argument(
        "command",
        choices=("run-scheduled", "run-now", "catch-up"),
    )
    args = parser.parse_args()
    if args.command == "catch-up":
        outcome = run_startup_catchup()
    else:
        outcome = run_scheduled_task(force=args.command == "run-now")
    print(outcome.model_dump_json())
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
