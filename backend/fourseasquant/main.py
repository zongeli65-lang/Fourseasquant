from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fourseasquant.akshare_market_data import (
    MarketDataQualityError,
    MarketDataSourceError,
    collect_and_store_daily_facts,
)
from fourseasquant.backfill import (
    BackfillAlreadyRunning,
    BackfillPreview,
    BackfillRequest,
    BackfillResponse,
    execute_backfill,
    preview_backfill,
)
from fourseasquant.candle_daily_task import execute_daily_task_with_candles
from fourseasquant.candlesticks import (
    CandleDataNotFound,
    CandleSeries,
    SecuritySearchResult,
    read_candle_series,
    search_eligible_securities,
)
from fourseasquant.automation import run_startup_catchup

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
from fourseasquant.real_market_dashboard import (
    OverviewCategory,
    OverviewSecurityPage,
    RealMarketDashboard,
    RealMarketDataNotFound,
    read_overview_securities,
    read_real_market_dashboard,
)
from fourseasquant.settings import (
    SettingsResponse,
    SettingsUpdate,
    read_settings,
    save_settings,
)
from fourseasquant.sector_leadership import membership_json_schema
from fourseasquant.technical_scoring import (
    TechnicalScoreStatus,
    TechnicalScoreView,
    read_technical_score_status,
    read_top_technical_scores,
)


APPLICATION_NAME = "Fourseasquant"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database(database_path())
    catchup_task: asyncio.Task[None] | None = None
    if os.environ.get("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0") == "1":
        catchup_task = asyncio.create_task(_run_startup_catchup())
    yield
    if catchup_task:
        await catchup_task


async def _run_startup_catchup() -> None:
    try:
        await asyncio.to_thread(run_startup_catchup)
    except Exception:
        # 启动补跑失败已有任务日志；不能阻止网站读取最近成功结果。
        pass


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


class MarketDataCollectionResponse(BaseModel):
    source: str
    requested_date: date
    actual_data_date: date
    benchmark: str
    benchmark_close: float
    security_count: int


class FailureSimulationRequest(BaseModel):
    target_date: date
    stage: FailureStage


class FailureBackfillRequest(BackfillRequest):
    failure_date: date
    failure_stage: FailureStage


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


@app.post(
    "/api/market-data/daily",
    response_model=MarketDataCollectionResponse,
    status_code=201,
)
def collect_daily_market_data(
    request: DailyTaskRequest,
) -> MarketDataCollectionResponse:
    path = database_path()
    settings = read_settings(path)
    try:
        facts = collect_and_store_daily_facts(
            request.target_date,
            path=path,
            new_stock_exclusion_days=settings.new_stock_exclusion_days,
        )
    except MarketDataQualityError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except MarketDataSourceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return MarketDataCollectionResponse(
        source=facts.source,
        requested_date=facts.requested_date,
        actual_data_date=facts.actual_data_date,
        benchmark=facts.benchmark.name,
        benchmark_close=facts.benchmark.close,
        security_count=len(facts.securities),
    )


@app.get(
    "/api/market-data/dashboard",
    response_model=RealMarketDashboard,
)
def real_market_dashboard(target_date: date) -> RealMarketDashboard:
    try:
        return read_real_market_dashboard(database_path(), target_date)
    except RealMarketDataNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    "/api/market-data/overview-securities",
    response_model=OverviewSecurityPage,
)
def overview_securities(
    target_date: date,
    category: OverviewCategory = "eligible",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    query: str = Query(default="", max_length=40),
) -> OverviewSecurityPage:
    try:
        return read_overview_securities(
            database_path(),
            requested_date=target_date,
            category=category,
            page=page,
            page_size=page_size,
            query=query,
        )
    except RealMarketDataNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/market-data/securities", response_model=list[SecuritySearchResult])
def security_search(
    target_date: date,
    query: str = "",
    limit: int = 20,
) -> list[SecuritySearchResult]:
    return search_eligible_securities(
        database_path(),
        query=query,
        requested_date=target_date,
        limit=limit,
    )


@app.get("/api/market-data/candles", response_model=CandleSeries)
def candle_series(
    instrument_type: Literal["stock", "index"],
    code: str,
    target_date: date,
    adjustment: Literal["raw", "qfq"] = "qfq",
) -> CandleSeries:
    try:
        return read_candle_series(
            database_path(),
            instrument_type=instrument_type,
            code=code,
            requested_date=target_date,
            adjustment=adjustment,
        )
    except CandleDataNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    "/api/technical-scores/status",
    response_model=TechnicalScoreStatus,
)
def technical_score_status() -> TechnicalScoreStatus:
    return read_technical_score_status(database_path())


@app.get(
    "/api/technical-scores/top",
    response_model=list[TechnicalScoreView],
)
def top_technical_scores(
    target_date: date,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TechnicalScoreView]:
    return read_top_technical_scores(
        database_path(),
        requested_date=target_date,
        limit=limit,
    )


@app.get("/api/contracts/sector-membership")
def sector_membership_contract() -> dict[str, object]:
    return membership_json_schema()


@app.post("/api/tasks/daily", response_model=TaskRunResponse, status_code=201)
def run_daily_task(request: DailyTaskRequest) -> TaskRunResponse:
    if os.environ.get("FOURSEASQUANT_ENABLE_FAILURE_SIMULATION") == "1":
        return execute_daily_task(request.target_date)
    return execute_daily_task_with_candles(request.target_date)


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

    @app.post(
        "/api/testing/backfill",
        response_model=BackfillResponse,
        status_code=201,
        include_in_schema=False,
    )
    def simulate_backfill_failure(
        request: FailureBackfillRequest,
    ) -> BackfillResponse:
        try:
            return execute_backfill(
                request,
                failure_date=request.failure_date,
                failure_stage=request.failure_stage,
            )
        except BackfillAlreadyRunning as error:
            raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/tasks/daily/retry", response_model=TaskRunResponse, status_code=201)
def retry_daily_task(request: DailyTaskRequest) -> TaskRunResponse:
    if os.environ.get("FOURSEASQUANT_ENABLE_FAILURE_SIMULATION") == "1":
        return execute_daily_task(request.target_date, trigger_method="retry")
    return execute_daily_task_with_candles(
        request.target_date,
        trigger_method="retry",
    )


@app.get("/api/tasks/history", response_model=list[TaskHistoryItem])
def task_history(limit: int = Query(default=20, ge=1, le=100)) -> list[TaskHistoryItem]:
    return read_task_history(limit=limit)


@app.get("/api/backfill/preview", response_model=BackfillPreview)
def get_backfill_preview(start_date: date, end_date: date) -> BackfillPreview:
    try:
        return preview_backfill(start_date, end_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/backfill", response_model=BackfillResponse, status_code=201)
def run_backfill(request: BackfillRequest) -> BackfillResponse:
    try:
        return execute_backfill(request)
    except BackfillAlreadyRunning as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
