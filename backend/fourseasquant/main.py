from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fourseasquant.daily_snapshots import (
    DashboardResponse,
    FailureStage,
    TaskHistoryItem,
    TaskRunResponse,
    execute_daily_task,
    read_dashboard,
    read_task_history,
)
from fourseasquant.database import (
    database_is_ready,
    database_path,
    initialize_database,
    snapshot_exists,
)
from fourseasquant.review_notes import (
    ReviewResponse,
    ReviewWriteRequest,
    read_review,
    save_review,
)
from fourseasquant.settings import (
    SettingsResponse,
    SettingsUpdate,
    read_settings,
    save_settings,
)


APPLICATION_NAME = "Fourseasquant"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database(database_path())
    yield


app = FastAPI(title=APPLICATION_NAME, lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("FOURSEASQUANT_WEB_ORIGIN", "http://127.0.0.1:5173")
    ],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    application: str
    status: str
    database: str


class DailyTaskRequest(BaseModel):
    target_date: date


class FailureSimulationRequest(BaseModel):
    target_date: date
    stage: FailureStage


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ready = database_is_ready(database_path())
    return HealthResponse(
        application=APPLICATION_NAME,
        status="ok" if ready else "degraded",
        database="ready" if ready else "unavailable",
    )


@app.get("/api/dashboard", response_model=DashboardResponse)
def dashboard(target_date: date) -> DashboardResponse:
    return read_dashboard(target_date)


@app.post("/api/tasks/daily", response_model=TaskRunResponse, status_code=201)
def run_daily_task(request: DailyTaskRequest) -> TaskRunResponse:
    return execute_daily_task(request.target_date)


if os.environ.get("FOURSEASQUANT_ENABLE_FAILURE_SIMULATION") == "1":

    @app.post(
        "/api/testing/tasks/daily",
        response_model=TaskRunResponse,
        status_code=201,
        include_in_schema=False,
    )
    def simulate_daily_task_failure(
        request: FailureSimulationRequest,
    ) -> TaskRunResponse:
        return execute_daily_task(
            request.target_date,
            simulate_failure_stage=request.stage,
        )


@app.post("/api/tasks/daily/retry", response_model=TaskRunResponse, status_code=201)
def retry_daily_task(request: DailyTaskRequest) -> TaskRunResponse:
    return execute_daily_task(request.target_date, trigger_method="retry")


@app.get("/api/tasks/history", response_model=list[TaskHistoryItem])
def task_history(limit: int = Query(default=20, ge=1, le=100)) -> list[TaskHistoryItem]:
    return read_task_history(limit=limit)


def review_database_path(review_date: date) -> Path:
    path = database_path()
    if not snapshot_exists(path, review_date):
        raise HTTPException(status_code=404, detail="该日期尚无已发布快照")
    return path


@app.get("/api/reviews/{review_date}", response_model=ReviewResponse)
def get_review(review_date: date) -> ReviewResponse:
    return read_review(review_database_path(review_date), review_date)


@app.put("/api/reviews/{review_date}", response_model=ReviewResponse)
def put_review(review_date: date, request: ReviewWriteRequest) -> ReviewResponse:
    return save_review(review_database_path(review_date), review_date, request)


@app.get("/api/settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    return read_settings(database_path())


@app.put("/api/settings", response_model=SettingsResponse)
def put_settings(settings: SettingsUpdate) -> SettingsResponse:
    return save_settings(database_path(), settings)


FRONTEND_DISTRIBUTION = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DISTRIBUTION.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DISTRIBUTION, html=True),
        name="frontend",
    )
