from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from threading import Event, Thread
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.database import database_path, initialize_database
from fourseasquant.fundamental_capital_actions import CapitalActionSnapshot
from fourseasquant.fundamental_repository import (
    FundamentalUpdateAttempt,
    claim_fundamental_update,
    read_latest_capital_action_batch,
    read_latest_published_capital_action_snapshot,
    read_monthly_snapshot_coverage,
    release_fundamental_update,
    renew_fundamental_update,
    save_capital_action_snapshot,
    save_fundamental_update_attempt,
)
from fourseasquant.settings import read_settings
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.trading_calendar import is_trading_day


BEIJING = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RETRY_MINUTES = (0, 10, 30, 60)
CommandRunner = Callable[[list[str], Path], int]
NowProvider = Callable[[], datetime]


class FundamentalAutomationOutcome(BaseModel):
    status: Literal["skipped", "succeeded", "failed"]
    target_date: date
    stage: Literal["schedule", "capital_actions", "monthly_snapshot", "complete"]
    reason: str


class FundamentalUpdateLease:
    def __init__(
        self,
        *,
        path: Path,
        target_date: date,
        claim_id: str,
        heartbeat_seconds: float,
        now: NowProvider,
    ) -> None:
        self._path = path
        self._target_date = target_date
        self._claim_id = claim_id
        self._heartbeat_seconds = heartbeat_seconds
        self._now = now
        self._stop = Event()
        self._thread = Thread(
            target=self._heartbeat_loop,
            name=f"fundamental-lease-{target_date.isoformat()}",
            daemon=True,
        )

    @classmethod
    def acquire(
        cls,
        path: Path,
        target_date: date,
        *,
        claimed_at: datetime,
        heartbeat_seconds: float = 60.0,
        now: NowProvider | None = None,
    ) -> FundamentalUpdateLease | None:
        claim_id = claim_fundamental_update(path, target_date, claimed_at)
        if claim_id is None:
            return None
        return cls(
            path=path,
            target_date=target_date,
            claim_id=claim_id,
            heartbeat_seconds=heartbeat_seconds,
            now=now or (lambda: datetime.now(BEIJING)),
        )

    def __enter__(self) -> FundamentalUpdateLease:
        self._thread.start()
        return self

    def __exit__(
        self,
        _error_type: object,
        _error: object,
        _traceback: object,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._heartbeat_seconds))
        release_fundamental_update(
            self._path,
            self._target_date,
            self._claim_id,
        )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            try:
                renewed = renew_fundamental_update(
                    self._path,
                    self._target_date,
                    self._claim_id,
                    self._now(),
                )
            except sqlite3.OperationalError:
                continue
            if not renewed:
                return


def run_scheduled_fundamental_update(
    *,
    now: datetime | None = None,
    path: Path | None = None,
    target_date: date | None = None,
    force: bool = False,
    ignore_schedule: bool = False,
    command_runner: CommandRunner | None = None,
) -> FundamentalAutomationOutcome:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    selected_path = path or database_path()
    initialize_database(selected_path)
    selected_date = target_date or current.date()
    if not is_trading_day(selected_date):
        return _outcome("skipped", selected_date, "schedule", "非交易日")
    lease = FundamentalUpdateLease.acquire(
        selected_path,
        selected_date,
        claimed_at=current,
    )
    if lease is None:
        return _outcome(
            "skipped",
            selected_date,
            "schedule",
            "该交易日基本面任务已在运行",
        )
    with lease:
        return _run_claimed_fundamental_update(
            current=current,
            selected_path=selected_path,
            selected_date=selected_date,
            force=force,
            ignore_schedule=ignore_schedule,
            command_runner=command_runner,
        )


def _run_claimed_fundamental_update(
    *,
    current: datetime,
    selected_path: Path,
    selected_date: date,
    force: bool,
    ignore_schedule: bool,
    command_runner: CommandRunner | None,
) -> FundamentalAutomationOutcome:
    scheduled_time = time.fromisoformat(
        read_settings(selected_path).auto_update_time
    )
    allowed_attempts = _allowed_attempts(current, scheduled_time)
    if not force and not ignore_schedule and allowed_attempts == 0:
        return _outcome(
            "skipped",
            selected_date,
            "schedule",
            "尚未到自动更新时间",
        )
    published = read_latest_published_capital_action_snapshot(
        selected_path,
        as_of_date=selected_date,
    )
    capital_ready = (
        published is not None and published.as_of_date == selected_date
    )
    monthly_due = _is_month_end_trading_day(selected_date)
    monthly_ready = (
        monthly_due
        and capital_ready
        and published is not None
        and set(published.expected_codes).issubset(
            read_monthly_snapshot_coverage(
                selected_path,
                as_of_date=selected_date,
                codes=published.expected_codes,
            )
        )
    )
    if (
        not force
        and capital_ready
        and (not monthly_due or monthly_ready)
    ):
        return _outcome(
            "skipped",
            selected_date,
            "complete",
            "该交易日资本行为已完整发布",
        )
    attempts = _failed_attempt_count(selected_path, selected_date)
    if (
        not force
        and not ignore_schedule
        and attempts >= allowed_attempts
    ):
        return _outcome(
            "skipped",
            selected_date,
            "schedule",
            (
                "自动重试已用尽，请在网站内手动重试"
                if allowed_attempts == len(RETRY_MINUTES)
                else "等待下一次自动重试"
            ),
        )

    runner = command_runner or _run_command
    if force or not capital_ready:
        capital_command = [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_fundamental_capital_actions.py"
            ),
            "--as-of",
            selected_date.isoformat(),
        ]
        capital_started_at = datetime.now(BEIJING)
        capital_result = runner(capital_command, selected_path)
        refreshed = read_latest_published_capital_action_snapshot(
            selected_path,
            as_of_date=selected_date,
        )
        if (
            capital_result != 0
            or refreshed is None
            or refreshed.as_of_date != selected_date
        ):
            _record_command_failure_if_needed(
                selected_path,
                target_date=selected_date,
                started_at=capital_started_at,
                message=f"资本行为命令退出码 {capital_result}",
            )
            return _outcome(
                "failed",
                selected_date,
                "capital_actions",
                "资本行为采集或完整性校验失败",
            )
        published = refreshed

    if monthly_due and (force or not monthly_ready):
        assert published is not None
        monthly_command = [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_fundamental_monthly_akshare.py"
            ),
            "--as-of",
            selected_date.isoformat(),
        ]
        monthly_result = runner(monthly_command, selected_path)
        coverage = read_monthly_snapshot_coverage(
            selected_path,
            as_of_date=selected_date,
            codes=published.expected_codes,
        )
        monthly_complete = set(published.expected_codes).issubset(coverage)
        if monthly_result != 0 or not monthly_complete:
            save_fundamental_update_attempt(
                selected_path,
                FundamentalUpdateAttempt(
                    target_date=selected_date,
                    stage="monthly_snapshot",
                    status="failed",
                    attempted_at=datetime.now(BEIJING),
                    error_summary=(
                        f"月度命令退出码 {monthly_result}；"
                        f"覆盖 {len(coverage)}/{len(published.expected_codes)}"
                    ),
                ),
            )
            return _outcome(
                "failed",
                selected_date,
                "monthly_snapshot",
                "月末基本面快照未完整生成",
            )
        save_fundamental_update_attempt(
            selected_path,
            FundamentalUpdateAttempt(
                target_date=selected_date,
                stage="monthly_snapshot",
                status="succeeded",
                attempted_at=datetime.now(BEIJING),
                error_summary=None,
            ),
        )
    return _outcome(
        "succeeded",
        selected_date,
        "complete",
        "资本行为已发布，月末时同步生成月度快照",
    )


def _run_command(command: list[str], path: Path) -> int:
    environment = os.environ.copy()
    environment["FOURSEASQUANT_DB_PATH"] = str(path)
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    return completed.returncode


def _record_command_failure_if_needed(
    path: Path,
    *,
    target_date: date,
    started_at: datetime,
    message: str,
) -> None:
    latest = read_latest_capital_action_batch(path, as_of_date=target_date)
    if (
        latest is not None
        and latest.as_of_date == target_date
        and latest.collected_at >= started_at
    ):
        return
    previous = read_latest_published_capital_action_snapshot(
        path,
        as_of_date=target_date,
    )
    save_capital_action_snapshot(
        path,
        CapitalActionSnapshot(
            as_of_date=target_date,
            expected_codes=(
                previous.expected_codes if previous is not None else []
            ),
            completed_codes=[],
            events=[],
            errors={"__global__": message},
        ),
        collected_at=datetime.now(BEIJING),
    )


def _failed_attempt_count(path: Path, target_date: date) -> int:
    with open_database_connection(path) as connection:
        capital_row = connection.execute(
            """
            SELECT COUNT(*)
            FROM capital_action_batches
            WHERE as_of_date = ? AND status = 'failed'
            """,
            (target_date.isoformat(),),
        ).fetchone()
        monthly_row = connection.execute(
            """
            SELECT COUNT(*)
            FROM fundamental_update_attempts
            WHERE target_date = ? AND status = 'failed'
            """,
            (target_date.isoformat(),),
        ).fetchone()
    return (
        (int(capital_row[0]) if capital_row is not None else 0)
        + (int(monthly_row[0]) if monthly_row is not None else 0)
    )


def _allowed_attempts(current: datetime, scheduled_time: time) -> int:
    base = datetime.combine(current.date(), scheduled_time, tzinfo=BEIJING)
    return sum(
        current >= base + timedelta(minutes=minutes)
        for minutes in RETRY_MINUTES
    )


def _is_month_end_trading_day(target_date: date) -> bool:
    candidate = target_date + timedelta(days=1)
    while candidate.month == target_date.month:
        if is_trading_day(candidate):
            return False
        candidate += timedelta(days=1)
    return True


def _outcome(
    status: Literal["skipped", "succeeded", "failed"],
    target_date: date,
    stage: Literal["schedule", "capital_actions", "monthly_snapshot", "complete"],
    reason: str,
) -> FundamentalAutomationOutcome:
    return FundamentalAutomationOutcome(
        status=status,
        target_date=target_date,
        stage=stage,
        reason=reason,
    )
    release_fundamental_update,
