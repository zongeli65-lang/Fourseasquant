from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.database import (
    create_task_run,
    database_path,
    fail_task_run,
    latest_snapshot,
    latest_task_status,
    publish_snapshot,
)
from fourseasquant.market_overview import MarketOverview
from fourseasquant.simulated_market_data import simulated_market_overview


TaskStatus = Literal["not_run", "running", "succeeded", "failed"]
SnapshotFactory = Callable[[date], Mapping[str, object]]


class MinimalSnapshot(BaseModel):
    source: str
    label: str
    seed: int
    market_overview: MarketOverview


class DashboardResponse(BaseModel):
    target_date: date
    actual_data_date: date | None
    last_updated_at: datetime | None
    task_status: TaskStatus
    snapshot: MinimalSnapshot | None


class TaskRunResponse(BaseModel):
    id: int
    trigger_method: Literal["manual"]
    target_date: date
    started_at: datetime
    finished_at: datetime
    status: Literal["succeeded"]


def simulated_snapshot(target_date: date) -> Mapping[str, object]:
    return {
        "source": "simulation",
        "label": "确定性模拟快照",
        "seed": int(target_date.strftime("%Y%m%d")),
        "market_overview": simulated_market_overview().model_dump(),
    }


def read_dashboard(target_date: date, *, path: Path | None = None) -> DashboardResponse:
    selected_path = path or database_path()
    snapshot_row = latest_snapshot(selected_path, target_date)
    task_status = cast(TaskStatus, latest_task_status(selected_path, target_date))
    if snapshot_row is None:
        return DashboardResponse(
            target_date=target_date,
            actual_data_date=None,
            last_updated_at=None,
            task_status=task_status,
            snapshot=None,
        )

    return DashboardResponse(
        target_date=target_date,
        actual_data_date=snapshot_row.actual_data_date,
        last_updated_at=snapshot_row.published_at,
        task_status=task_status,
        snapshot=MinimalSnapshot.model_validate_json(snapshot_row.payload_json),
    )


def execute_daily_task(
    target_date: date,
    *,
    path: Path | None = None,
    snapshot_factory: SnapshotFactory = simulated_snapshot,
) -> TaskRunResponse:
    selected_path = path or database_path()
    started_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    task_id = create_task_run(selected_path, target_date, started_at)

    try:
        snapshot = MinimalSnapshot.model_validate(snapshot_factory(target_date))
        finished_at = datetime.now(ZoneInfo("Asia/Shanghai"))
        payload_json = json.dumps(
            snapshot.model_dump(), ensure_ascii=False, sort_keys=True
        )
        publish_snapshot(
            selected_path,
            task_id=task_id,
            target_date=target_date,
            payload_json=payload_json,
            published_at=finished_at,
        )
    except Exception:
        fail_task_run(
            selected_path,
            task_id,
            datetime.now(ZoneInfo("Asia/Shanghai")),
        )
        raise

    return TaskRunResponse(
        id=task_id,
        trigger_method="manual",
        target_date=target_date,
        started_at=started_at,
        finished_at=finished_at,
        status="succeeded",
    )
