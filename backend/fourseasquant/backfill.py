from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from threading import Lock
from collections.abc import Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, model_validator

from fourseasquant.daily_snapshots import (
    FailureStage,
    PreparedMarket,
    TaskRunResponse,
    execute_daily_task,
    prepare_simulated_market,
)
from fourseasquant.database import database_path
from fourseasquant.settings import read_settings
from fourseasquant.trading_calendar import (
    CALENDAR_SUPPORTED_END,
    CALENDAR_SUPPORTED_START,
    trading_days_between,
)


MAX_BACKFILL_CALENDAR_DAYS = 1_100
_BACKFILL_LOCK = Lock()
AfterEachHook = Callable[[int], None]


class BackfillAlreadyRunning(RuntimeError):
    pass


class BackfillRequest(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> BackfillRequest:
        validate_backfill_range(self.start_date, self.end_date)
        return self


class BackfillPreview(BaseModel):
    start_date: date
    end_date: date
    trading_day_count: int
    trading_days: list[date]


class BackfillResponse(BaseModel):
    start_date: date
    end_date: date
    total: int
    succeeded: int
    failed: int
    results: list[TaskRunResponse]


def validate_backfill_range(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise ValueError("补算结束日期不能早于开始日期")
    if (end_date - start_date).days > MAX_BACKFILL_CALENDAR_DAYS:
        raise ValueError("单次补算范围不能超过约三年")
    if start_date < CALENDAR_SUPPORTED_START:
        raise ValueError("交易日历仅支持 2023 年及以后")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    latest_supported_date = min(today, CALENDAR_SUPPORTED_END)
    if end_date > latest_supported_date:
        if end_date > CALENDAR_SUPPORTED_END:
            raise ValueError("交易日历尚未覆盖 2026 年以后")
        raise ValueError("不能补算未来日期")


def preview_backfill(start_date: date, end_date: date) -> BackfillPreview:
    validate_backfill_range(start_date, end_date)
    trading_days = trading_days_between(start_date, end_date)
    return BackfillPreview(
        start_date=start_date,
        end_date=end_date,
        trading_day_count=len(trading_days),
        trading_days=trading_days,
    )


def execute_backfill(
    request: BackfillRequest,
    *,
    path: Path | None = None,
    failure_date: date | None = None,
    failure_stage: FailureStage | None = None,
    after_each: AfterEachHook | None = None,
) -> BackfillResponse:
    if not _BACKFILL_LOCK.acquire(blocking=False):
        raise BackfillAlreadyRunning("已有补算任务正在运行")
    try:
        return _execute_backfill_locked(
            request,
            path=path,
            failure_date=failure_date,
            failure_stage=failure_stage,
            after_each=after_each,
        )
    finally:
        _BACKFILL_LOCK.release()


def _execute_backfill_locked(
    request: BackfillRequest,
    *,
    path: Path | None,
    failure_date: date | None,
    failure_stage: FailureStage | None,
    after_each: AfterEachHook | None,
) -> BackfillResponse:
    selected_path = path or database_path()
    captured_settings = read_settings(selected_path)

    def frozen_market_factory(target_date: date, database: Path) -> PreparedMarket:
        return prepare_simulated_market(
            target_date,
            database,
            settings_override=captured_settings,
        )

    results: list[TaskRunResponse] = []
    for target_date in trading_days_between(request.start_date, request.end_date):
        simulated_stage = (
            failure_stage if target_date == failure_date else None
        )
        result = execute_daily_task(
            target_date,
            path=selected_path,
            trigger_method="backfill",
            simulate_failure_stage=simulated_stage,
            market_stage_factory=frozen_market_factory,
            propagate_unexpected=False,
        )
        results.append(result)
        if after_each:
            after_each(len(results))
    succeeded = sum(result.status == "succeeded" for result in results)
    return BackfillResponse(
        start_date=request.start_date,
        end_date=request.end_date,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )
