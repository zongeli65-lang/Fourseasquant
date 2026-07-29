from __future__ import annotations

from datetime import date, datetime

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from fourseasquant.database import database_path
from fourseasquant.public_opinion import BEIJING, OpinionPlatform
from fourseasquant.public_opinion_repository import (
    OpinionWatchlistEntry,
    PublicOpinionCollectionJob,
    PublicOpinionOverviewItem,
    PublicOpinionWindow,
    StrategyOpinionTargetSnapshot,
    add_to_opinion_watchlist,
    create_manual_collection_jobs,
    list_public_opinion_jobs,
    publish_strategy_opinion_targets,
    read_public_opinion_overview,
    read_public_opinion_window,
    read_strategy_opinion_targets,
    remove_from_opinion_watchlist,
    schedule_automatic_collection_jobs,
)
from fourseasquant.public_opinion_collection import run_public_opinion_job
from fourseasquant.public_opinion_worker import (
    PublicOpinionBatchController,
    PublicOpinionBatchStatus,
)
from fourseasquant.public_opinion_secrets import (
    DeepSeekApiConfigurationStatus,
    clear_deepseek_runtime_api_key,
    read_deepseek_api_configuration,
    set_deepseek_runtime_api_key,
)


router = APIRouter(prefix="/api/public-opinion", tags=["舆论监测"])
batch_controller = PublicOpinionBatchController()


class WatchlistCreateRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)


class PublicOpinionOverviewResponse(BaseModel):
    items: list[PublicOpinionOverviewItem]


class AutomaticScheduleRequest(BaseModel):
    actual_date: date


class StrategyTargetsPublishRequest(BaseModel):
    actual_date: date
    strategy_version: str = Field(min_length=1, max_length=120)
    codes: list[str] = Field(max_length=10)


class ManualInvestigationRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    start_date: date
    end_date: date


class DeepSeekApiConfigurationRequest(BaseModel):
    api_key: SecretStr


class PlatformOpinionWindows(BaseModel):
    platform: OpinionPlatform
    today: PublicOpinionWindow
    three_day: PublicOpinionWindow
    seven_day: PublicOpinionWindow


class SecurityOpinionWindowsResponse(BaseModel):
    code: str
    end_date: date
    platforms: list[PlatformOpinionWindows]


@router.get("/overview", response_model=PublicOpinionOverviewResponse)
def get_public_opinion_overview() -> PublicOpinionOverviewResponse:
    return PublicOpinionOverviewResponse(
        items=read_public_opinion_overview(
            database_path(),
            as_of_date=datetime.now(BEIJING).date(),
        )
    )


@router.get(
    "/securities/{code}/windows",
    response_model=SecurityOpinionWindowsResponse,
)
def get_security_opinion_windows(
    code: str,
    end_date: date,
) -> SecurityOpinionWindowsResponse:
    try:
        platforms: tuple[OpinionPlatform, ...] = (
            "eastmoney",
            "sina",
            "tonghuashun",
        )
        return SecurityOpinionWindowsResponse(
            code=code,
            end_date=end_date,
            platforms=[
                PlatformOpinionWindows(
                    platform=platform,
                    today=read_public_opinion_window(
                        database_path(),
                        platform=platform,
                        code=code,
                        end_date=end_date,
                        days=1,
                    ),
                    three_day=read_public_opinion_window(
                        database_path(),
                        platform=platform,
                        code=code,
                        end_date=end_date,
                        days=3,
                    ),
                    seven_day=read_public_opinion_window(
                        database_path(),
                        platform=platform,
                        code=code,
                        end_date=end_date,
                        days=7,
                    ),
                )
                for platform in platforms
            ],
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/watchlist",
    response_model=OpinionWatchlistEntry,
    status_code=201,
)
def create_watchlist_entry(
    request: WatchlistCreateRequest,
) -> OpinionWatchlistEntry:
    try:
        return add_to_opinion_watchlist(
            database_path(),
            code=request.code,
            name=request.name,
            now=datetime.now().astimezone(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete(
    "/watchlist/{code}",
    response_model=OpinionWatchlistEntry,
)
def delete_watchlist_entry(code: str) -> OpinionWatchlistEntry:
    try:
        return remove_from_opinion_watchlist(
            database_path(),
            code=code,
            now=datetime.now().astimezone(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/jobs", response_model=list[PublicOpinionCollectionJob])
def get_public_opinion_jobs() -> list[PublicOpinionCollectionJob]:
    return list_public_opinion_jobs(database_path())


@router.put(
    "/strategy-targets",
    response_model=StrategyOpinionTargetSnapshot,
)
def put_strategy_opinion_targets(
    request: StrategyTargetsPublishRequest,
) -> StrategyOpinionTargetSnapshot:
    try:
        return publish_strategy_opinion_targets(
            database_path(),
            actual_date=request.actual_date,
            strategy_version=request.strategy_version,
            codes=request.codes,
            published_at=datetime.now().astimezone(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/strategy-targets/latest",
    response_model=StrategyOpinionTargetSnapshot | None,
)
def get_strategy_opinion_targets(
    actual_date: date,
) -> StrategyOpinionTargetSnapshot | None:
    return read_strategy_opinion_targets(
        database_path(),
        actual_date=actual_date,
    )


@router.post(
    "/manual-jobs",
    response_model=list[PublicOpinionCollectionJob],
    status_code=201,
)
def create_manual_investigation(
    request: ManualInvestigationRequest,
) -> list[PublicOpinionCollectionJob]:
    try:
        return create_manual_collection_jobs(
            database_path(),
            platform="sina",
            code=request.code,
            start_date=request.start_date,
            end_date=request.end_date,
            now=datetime.now().astimezone(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/jobs/batch-status",
    response_model=PublicOpinionBatchStatus,
)
def get_batch_status() -> PublicOpinionBatchStatus:
    return batch_controller.status()


@router.get(
    "/api-configuration",
    response_model=DeepSeekApiConfigurationStatus,
)
def get_api_configuration() -> DeepSeekApiConfigurationStatus:
    return read_deepseek_api_configuration()


@router.put(
    "/api-configuration",
    response_model=DeepSeekApiConfigurationStatus,
)
def put_api_configuration(
    request: DeepSeekApiConfigurationRequest,
) -> DeepSeekApiConfigurationStatus:
    if batch_controller.status().running:
        raise HTTPException(
            status_code=409,
            detail="舆论批次运行中，不能更换 API 密钥",
        )
    try:
        return set_deepseek_runtime_api_key(
            request.api_key.get_secret_value(),
            updated_at=datetime.now().astimezone(),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.delete(
    "/api-configuration",
    response_model=DeepSeekApiConfigurationStatus,
)
def delete_api_configuration() -> DeepSeekApiConfigurationStatus:
    if batch_controller.status().running:
        raise HTTPException(
            status_code=409,
            detail="舆论批次运行中，不能清除 API 密钥",
        )
    try:
        return clear_deepseek_runtime_api_key()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post(
    "/jobs/run-batch",
    response_model=PublicOpinionBatchStatus,
    status_code=202,
)
def start_batch() -> PublicOpinionBatchStatus:
    return batch_controller.start(database_path())


@router.post(
    "/jobs/stop-batch",
    response_model=PublicOpinionBatchStatus,
)
def stop_batch() -> PublicOpinionBatchStatus:
    return batch_controller.stop()


@router.post(
    "/schedule",
    response_model=list[PublicOpinionCollectionJob],
    status_code=201,
)
def create_automatic_schedule(
    request: AutomaticScheduleRequest,
) -> list[PublicOpinionCollectionJob]:
    return schedule_automatic_collection_jobs(
        database_path(),
        actual_date=request.actual_date,
        now=datetime.now().astimezone(),
    )


@router.post(
    "/jobs/{job_id}/run",
    response_model=PublicOpinionCollectionJob,
)
def run_collection_job(job_id: int) -> PublicOpinionCollectionJob:
    if batch_controller.status().running:
        raise HTTPException(
            status_code=409,
            detail="后台批次正在运行，不能并发执行单任务",
        )
    try:
        with httpx.Client(
            timeout=httpx.Timeout(120, connect=20),
            follow_redirects=True,
        ) as client:
            return run_public_opinion_job(
                database_path(),
                job_id=job_id,
                now=datetime.now().astimezone(),
                client=client,
            )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
