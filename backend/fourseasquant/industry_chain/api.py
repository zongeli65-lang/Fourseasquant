from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from fourseasquant.database import database_path
from fourseasquant.industry_chain.control import (
    HuntingDisabledError,
    HuntingRequestRecord,
    RuntimeStatus,
    read_hunting_requests,
    read_runtime_status,
    set_hunting_enabled,
    submit_manual_hunt,
)
from fourseasquant.industry_chain.source_registry import (
    RegisteredSource,
    read_latest_sources,
)
from fourseasquant.industry_chain.repository import read_recent_selections
from fourseasquant.industry_chain.retention import (
    SelectionNotFoundError,
    dismiss_selection,
)


router = APIRouter(prefix="/api/industry-chain", tags=["产业链龙头"])
BEIJING = ZoneInfo("Asia/Shanghai")


class RuntimeControlUpdate(BaseModel):
    enabled: bool


class RuntimeStatusResponse(BaseModel):
    enabled: bool
    state: str
    paused_at: datetime | None
    resumed_at: datetime | None
    catchup_from: datetime | None
    worker_heartbeat_at: datetime | None
    worker_online: bool
    last_poll_at: datetime | None
    last_model_run_at: datetime | None
    last_cleanup_at: datetime | None
    error_summary: str | None
    queued_count: int
    running_count: int
    discovered_count: int
    triaged_discovery_count: int
    untriaged_count: int
    research_material_count: int
    event_cluster_count: int
    investigated_company_count: int
    freshness_cutoff: datetime
    deep_hunt_count: int
    completed_selection_count: int
    invalid_event_count: int
    failed_hunt_count: int
    published_count: int
    next_scheduled_scan_at: datetime | None
    updated_at: datetime


class ManualHuntRequest(BaseModel):
    trigger_type: Literal["keyword", "url", "message"]
    content: str = Field(min_length=1, max_length=10_000)


class HuntResponse(BaseModel):
    request_id: str
    trigger_method: str
    trigger_type: str
    trigger_content: str
    source_url: str | None
    as_of_time: datetime
    priority: int
    status: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_summary: str | None


class SourceResponse(BaseModel):
    source_id: str
    source_version: int
    source_name: str
    base_url: str
    domain: str
    source_tier: int
    source_type: str
    categories: list[str]
    access_class: str
    lifecycle_state: str
    poll_interval_minutes: int
    allow_browser: bool
    config_version: str
    effective_at: datetime


class SelectionDismissResponse(BaseModel):
    selection_id: str
    selection_version: int
    deleted: bool
    dismissed_at: datetime


@router.get("/status", response_model=RuntimeStatusResponse)
def get_runtime_status() -> RuntimeStatusResponse:
    return _status_response(read_runtime_status(database_path()))


@router.put("/control", response_model=RuntimeStatusResponse)
def update_runtime_control(
    update: RuntimeControlUpdate,
) -> RuntimeStatusResponse:
    return _status_response(
        set_hunting_enabled(
            database_path(),
            enabled=update.enabled,
        )
    )


@router.post("/hunts", response_model=HuntResponse, status_code=201)
def create_manual_hunt(request: ManualHuntRequest) -> HuntResponse:
    try:
        record = submit_manual_hunt(
            database_path(),
            trigger_type=request.trigger_type,
            content=request.content,
        )
    except HuntingDisabledError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _hunt_response(record)


@router.get("/hunts", response_model=list[HuntResponse])
def get_hunts(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[HuntResponse]:
    return [
        _hunt_response(record)
        for record in read_hunting_requests(database_path(), limit=limit)
    ]


@router.get("/sources", response_model=list[SourceResponse])
def get_sources() -> list[SourceResponse]:
    return [_source_response(source) for source in read_latest_sources(database_path())]


@router.get("/selections", response_model=list[dict[str, object]])
def get_selections(
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict[str, object]]:
    return list(read_recent_selections(database_path(), limit=limit))


@router.delete(
    "/selections/{selection_id}/{selection_version}",
    response_model=SelectionDismissResponse,
)
def delete_selection(
    selection_id: str,
    selection_version: int,
) -> SelectionDismissResponse:
    try:
        result = dismiss_selection(
            database_path(),
            selection_id=selection_id,
            selection_version=selection_version,
            now=datetime.now(BEIJING),
        )
    except SelectionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SelectionDismissResponse(
        selection_id=result.selection_id,
        selection_version=result.selection_version,
        deleted=True,
        dismissed_at=result.dismissed_at,
    )


def _status_response(status: RuntimeStatus) -> RuntimeStatusResponse:
    return RuntimeStatusResponse(**status.__dict__)


def _hunt_response(record: HuntingRequestRecord) -> HuntResponse:
    return HuntResponse(**record.__dict__)


def _source_response(source: RegisteredSource) -> SourceResponse:
    return SourceResponse(
        source_id=source.source_id,
        source_version=source.source_version,
        source_name=source.source_name,
        base_url=source.base_url,
        domain=source.domain,
        source_tier=source.source_tier,
        source_type=source.source_type,
        categories=list(source.categories),
        access_class=source.access_class,
        lifecycle_state=source.lifecycle_state,
        poll_interval_minutes=source.poll_interval_minutes,
        allow_browser=source.allow_browser,
        config_version=source.config_version,
        effective_at=source.effective_at,
    )
