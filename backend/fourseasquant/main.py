from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fourseasquant.daily_snapshots import (
    DashboardResponse,
    TaskRunResponse,
    execute_daily_task,
    read_dashboard,
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


APPLICATION_NAME = "Fourseasquant"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database(database_path())
    yield


app = FastAPI(title=APPLICATION_NAME, lifespan=lifespan)
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


FRONTEND_DISTRIBUTION = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DISTRIBUTION.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DISTRIBUTION, html=True),
        name="frontend",
    )
