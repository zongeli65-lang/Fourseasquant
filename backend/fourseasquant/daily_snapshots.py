from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.database import (
    TechnicalScorePublicationRow,
    create_task_run,
    database_path,
    fail_task_run,
    latest_task_run,
    latest_snapshot,
    latest_task_status,
    publish_snapshot,
    task_runs,
    update_task_stage,
)
from fourseasquant.market_overview import MarketOverview
from fourseasquant.simulated_market_data import (
    simulated_market_overview,
)
from fourseasquant.strategy_performance import (
    StrategyPerformance,
)
from fourseasquant.simulated_strategy_data import simulated_strategy_performance
from fourseasquant.portfolio_review import PortfolioReview
from fourseasquant.simulated_portfolio_data import simulated_portfolio_review
from fourseasquant.settings import SettingsResponse, read_settings
from fourseasquant.task_logging import log_task_event


TaskStatus = Literal["not_run", "running", "succeeded", "failed"]
TaskTrigger = Literal["manual", "retry", "backfill", "scheduled"]
FailureStage = Literal[
    "market_prepare",
    "strategy_run",
    "output_validation",
    "transactional_publish",
]
MarketStageFactory = Callable[[date, Path], "PreparedMarket"]
StrategyStageFactory = Callable[[date, "PreparedMarket"], Mapping[str, object]]

STAGE_LABELS: dict[str, str] = {
    "queued": "排队",
    "market_prepare": "市场准备",
    "strategy_run": "策略运行",
    "output_validation": "输出校验",
    "transactional_publish": "事务发布",
    "completed": "完成",
    "unknown": "历史记录",
}


class SimulatedStageFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class SimulationAdapterProfile:
    label: str
    turnover_scale: float
    strategy_return_scale: float


@dataclass(frozen=True)
class PreparedMarket:
    payload: Mapping[str, object]
    benchmark: str
    adapter: SimulationAdapterProfile
    technical_publication: TechnicalScorePublicationRow | None = None


SIMULATION_ADAPTERS = {
    "simulation": SimulationAdapterProfile("确定性模拟快照", 1.0, 1.0),
    "simulation_conservative": SimulationAdapterProfile(
        "保守模拟快照", 0.9, 0.75
    ),
}


class MinimalSnapshot(BaseModel):
    source: str
    label: str
    seed: int
    market_overview: MarketOverview
    strategy_performance: StrategyPerformance
    portfolio_review: PortfolioReview


class FailureSummary(BaseModel):
    stage: str
    stage_label: str
    failed_at: datetime
    error_summary: str


class DashboardResponse(BaseModel):
    target_date: date
    actual_data_date: date | None
    last_updated_at: datetime | None
    task_status: TaskStatus
    failure: FailureSummary | None
    snapshot: MinimalSnapshot | None


class TaskRunResponse(BaseModel):
    id: int
    trigger_method: TaskTrigger
    target_date: date
    started_at: datetime
    finished_at: datetime
    stage: str
    stage_label: str
    status: Literal["succeeded", "failed"]
    error_summary: str | None = None


class TaskHistoryItem(BaseModel):
    id: int
    trigger_method: str
    target_date: date
    started_at: datetime
    finished_at: datetime | None
    stage: str
    stage_label: str
    status: str
    error_summary: str | None


def prepare_simulated_market(
    target_date: date,
    path: Path,
    *,
    settings_override: SettingsResponse | None = None,
) -> PreparedMarket:
    settings = settings_override or read_settings(path)
    adapter = SIMULATION_ADAPTERS[settings.data_adapter]
    return PreparedMarket(
        payload={
            "source": settings.data_adapter,
            "label": adapter.label,
            "seed": int(target_date.strftime("%Y%m%d")),
            "market_overview": simulated_market_overview(
                settings.new_stock_exclusion_days,
                turnover_scale=adapter.turnover_scale,
            ).model_dump(),
        },
        benchmark=settings.benchmark,
        adapter=adapter,
    )


def run_simulated_strategy(
    target_date: date,
    prepared_market: PreparedMarket,
) -> Mapping[str, object]:
    strategy_performance = simulated_strategy_performance(
        target_date,
        prepared_market.benchmark,
        strategy_return_scale=prepared_market.adapter.strategy_return_scale,
    )
    return {
        **prepared_market.payload,
        "strategy_performance": strategy_performance.model_dump(),
        "portfolio_review": simulated_portfolio_review(
            target_date,
            strategy_performance.daily_summary.strategy_return_pct,
        ).model_dump(),
    }


def read_dashboard(target_date: date, *, path: Path | None = None) -> DashboardResponse:
    selected_path = path or database_path()
    snapshot_row = latest_snapshot(selected_path, target_date)
    task_status = cast(TaskStatus, latest_task_status(selected_path, target_date))
    latest_run = latest_task_run(selected_path, target_date)
    failure = (
        FailureSummary(
            stage=latest_run.stage,
            stage_label=STAGE_LABELS.get(latest_run.stage, latest_run.stage),
            failed_at=latest_run.finished_at,
            error_summary=latest_run.error_summary or "任务执行失败",
        )
        if latest_run and latest_run.status == "failed" and latest_run.finished_at
        else None
    )
    if snapshot_row is None:
        return DashboardResponse(
            target_date=target_date,
            actual_data_date=None,
            last_updated_at=None,
            task_status=task_status,
            failure=failure,
            snapshot=None,
        )

    return DashboardResponse(
        target_date=target_date,
        actual_data_date=snapshot_row.actual_data_date,
        last_updated_at=snapshot_row.published_at,
        task_status=task_status,
        failure=failure,
        snapshot=MinimalSnapshot.model_validate_json(snapshot_row.payload_json),
    )


def read_task_history(*, path: Path | None = None, limit: int = 20) -> list[TaskHistoryItem]:
    return [
        TaskHistoryItem(
            id=row.id,
            trigger_method=row.trigger_method,
            target_date=row.target_date,
            started_at=row.started_at,
            finished_at=row.finished_at,
            stage=row.stage,
            stage_label=STAGE_LABELS.get(row.stage, row.stage),
            status=row.status,
            error_summary=row.error_summary,
        )
        for row in task_runs(path or database_path(), limit=limit)
    ]


def execute_daily_task(
    target_date: date,
    *,
    path: Path | None = None,
    market_stage_factory: MarketStageFactory = prepare_simulated_market,
    strategy_stage_factory: StrategyStageFactory = run_simulated_strategy,
    trigger_method: TaskTrigger = "manual",
    simulate_failure_stage: FailureStage | None = None,
    propagate_unexpected: bool = True,
) -> TaskRunResponse:
    selected_path = path or database_path()
    started_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    task_id = create_task_run(
        selected_path,
        target_date,
        started_at,
        trigger_method=trigger_method,
    )
    current_stage: FailureStage = "market_prepare"

    try:
        update_task_stage(selected_path, task_id, current_stage)
        _raise_simulated_failure(current_stage, simulate_failure_stage)
        prepared_market = market_stage_factory(target_date, selected_path)

        current_stage = "strategy_run"
        update_task_stage(selected_path, task_id, current_stage)
        _raise_simulated_failure(current_stage, simulate_failure_stage)
        candidate = strategy_stage_factory(target_date, prepared_market)

        current_stage = "output_validation"
        update_task_stage(selected_path, task_id, current_stage)
        _raise_simulated_failure(current_stage, simulate_failure_stage)
        snapshot = MinimalSnapshot.model_validate(candidate)

        current_stage = "transactional_publish"
        update_task_stage(selected_path, task_id, current_stage)
        finished_at = datetime.now(ZoneInfo("Asia/Shanghai"))
        payload_json = json.dumps(
            snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        publish_snapshot(
            selected_path,
            task_id=task_id,
            target_date=target_date,
            payload_json=payload_json,
            published_at=finished_at,
            simulate_failure=simulate_failure_stage == current_stage,
            technical_publication=prepared_market.technical_publication,
        )
    except Exception as error:
        failed_at = datetime.now(ZoneInfo("Asia/Shanghai"))
        is_simulated = simulate_failure_stage == current_stage
        error_summary = (
            f"模拟触发：{STAGE_LABELS[current_stage]}失败"
            if is_simulated
            else "任务执行异常，请查看本机日志"
        )
        fail_task_run(
            selected_path,
            task_id,
            failed_at,
            stage=current_stage,
            error_summary=error_summary,
        )
        _safe_log_task_event(
            event="task_failed",
            task_id=task_id,
            target_date=target_date,
            stage=current_stage,
            occurred_at=failed_at,
            trigger_method=trigger_method,
            error_type=type(error).__name__,
        )
        if is_simulated or not propagate_unexpected:
            return TaskRunResponse(
                id=task_id,
                trigger_method=trigger_method,
                target_date=target_date,
                started_at=started_at,
                finished_at=failed_at,
                stage=current_stage,
                stage_label=STAGE_LABELS[current_stage],
                status="failed",
                error_summary=error_summary,
            )
        raise

    _safe_log_task_event(
        event="task_succeeded",
        task_id=task_id,
        target_date=target_date,
        stage="completed",
        occurred_at=finished_at,
        trigger_method=trigger_method,
    )
    return TaskRunResponse(
        id=task_id,
        trigger_method=trigger_method,
        target_date=target_date,
        started_at=started_at,
        finished_at=finished_at,
        stage="completed",
        stage_label=STAGE_LABELS["completed"],
        status="succeeded",
    )


def _raise_simulated_failure(
    current_stage: FailureStage,
    requested_stage: FailureStage | None,
) -> None:
    if current_stage == requested_stage:
        raise SimulatedStageFailure(current_stage)


def _safe_log_task_event(
    *,
    event: str,
    task_id: int,
    target_date: date,
    stage: str,
    occurred_at: datetime,
    trigger_method: str,
    error_type: str | None = None,
) -> None:
    try:
        log_task_event(
            event=event,
            task_id=task_id,
            target_date=target_date,
            stage=stage,
            occurred_at=occurred_at,
            trigger_method=trigger_method,
            error_type=error_type,
        )
    except Exception:
        # 日志是尽力而为的诊断副作用，绝不能改变已提交的业务结果。
        return
