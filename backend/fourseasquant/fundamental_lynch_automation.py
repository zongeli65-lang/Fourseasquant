from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.database import database_path, initialize_database
from fourseasquant.fundamental_lynch_repository import (
    read_latest_published_lynch_daily_batch,
    read_latest_published_lynch_financial_batch,
)
from fourseasquant.fundamental_lynch_service import (
    read_lynch_market_universe,
)


BEIJING = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CommandRunner = Callable[[list[str], Path], int]


class LynchAutomationOutcome(BaseModel):
    status: Literal["skipped", "succeeded", "failed"]
    target_date: date
    stage: Literal["financial_base", "daily_calculation", "complete"]
    reason: str


def run_scheduled_lynch_update(
    *,
    path: Path | None = None,
    target_date: date | None = None,
    force: bool = False,
    command_runner: CommandRunner | None = None,
) -> LynchAutomationOutcome:
    selected_path = path or database_path()
    initialize_database(selected_path)
    requested = target_date or datetime.now(BEIJING).date()
    try:
        universe = read_lynch_market_universe(selected_path, requested)
    except LookupError as error:
        return _outcome("failed", requested, "daily_calculation", str(error))
    selected_date = universe.actual_data_date
    daily = read_latest_published_lynch_daily_batch(
        selected_path, selected_date
    )
    if not force and daily is not None and daily.target_date == selected_date:
        return _outcome(
            "skipped", selected_date, "complete", "该交易日林奇结果已发布"
        )
    runner = command_runner or _run_command
    financial = read_latest_published_lynch_financial_batch(
        selected_path, selected_date
    )
    base_due = (
        financial is None
        or (selected_date - financial.target_date).days >= 32
    )
    if base_due:
        command = [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_lynch_financial_base_akshare.py"
            ),
            "--as-of",
            selected_date.isoformat(),
        ]
        return_code = runner(command, selected_path)
        financial = read_latest_published_lynch_financial_batch(
            selected_path, selected_date
        )
        if (
            return_code != 0
            or financial is None
            or financial.target_date != selected_date
        ):
            return _outcome(
                "failed",
                selected_date,
                "financial_base",
                "全市场财务基座采集或完整性校验失败",
            )
    daily_command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts" / "calculate_lynch_daily.py"),
        "--target-date",
        selected_date.isoformat(),
    ]
    daily_return_code = runner(daily_command, selected_path)
    refreshed = read_latest_published_lynch_daily_batch(
        selected_path, selected_date
    )
    if (
        daily_return_code != 0
        or refreshed is None
        or refreshed.target_date != selected_date
    ):
        return _outcome(
            "failed",
            selected_date,
            "daily_calculation",
            "林奇日频全量重算或完整性校验失败",
        )
    return _outcome(
        "succeeded",
        selected_date,
        "complete",
        "全市场林奇日频结果完整发布",
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


def _outcome(
    status: Literal["skipped", "succeeded", "failed"],
    target_date: date,
    stage: Literal["financial_base", "daily_calculation", "complete"],
    reason: str,
) -> LynchAutomationOutcome:
    return LynchAutomationOutcome(
        status=status,
        target_date=target_date,
        stage=stage,
        reason=reason,
    )
