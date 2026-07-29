from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    CandleAvailability,
    CandleDataNotFound,
    CandleSeries,
    SecuritySearchResult,
    read_candle_availability,
    read_candle_series,
    search_eligible_securities,
)
from fourseasquant.core_strategy_api import router as core_strategy_router
from fourseasquant.core_strategy_runtime import (
    advance_core_strategy_automation,
    next_core_strategy_automation_date,
)
from fourseasquant.automation import (
    BEIJING,
    TargetDateClaimLease,
    resolve_manual_target_date,
    run_startup_catchup,
)

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
from fourseasquant.fundamental_discovery import BoardCandidate
from fourseasquant.fundamental_automation import (
    FundamentalAutomationOutcome,
    run_scheduled_fundamental_update,
)
from fourseasquant.fundamental_queries import (
    BoardCandidatePublication,
    CapitalActionEvidence,
    CapitalActionStatus,
    DiscussionDayView,
    DiscussionSeries,
    FundamentalOverview,
    MonthlyFundamentalRecord,
    MonthlyFundamentalSeries,
    OverviewSortField,
    SortOrder,
    read_board_candidate,
    read_capital_action_evidence,
    read_capital_action_status,
    read_discussion_series,
    read_fundamental_overview,
    read_latest_board_candidate_publication,
    read_latest_discussion,
    read_latest_monthly_fundamental,
    read_monthly_fundamental_series,
)
from fourseasquant.fundamental_lynch_queries import (
    LynchMarketOverview,
    LynchSortField,
    read_lynch_market_overview,
)
from fourseasquant.fundamental_lynch_automation import (
    LynchAutomationOutcome,
    run_scheduled_lynch_update,
)
from fourseasquant.industry_chain.api import router as industry_chain_router
from fourseasquant.market_environment import (
    MarketEnvironmentHistory,
    MarketEnvironmentRefreshResult,
    MarketEnvironmentSnapshot,
    MarketEnvironmentUnavailable,
    read_market_environment,
    read_market_environment_history,
    refresh_market_environment,
)
from fourseasquant.public_opinion_api import (
    batch_controller,
    router as public_opinion_router,
)
from fourseasquant.public_opinion_secrets import (
    clear_deepseek_runtime_cache,
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
    TechnicalScorePage,
    TechnicalScoreSortField,
    TechnicalScoreSortOrder,
    TechnicalScoreStatus,
    TechnicalScoreView,
    read_technical_score_page,
    read_technical_score_status,
    read_top_technical_scores,
)


APPLICATION_NAME = "Fourseasquant"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    clear_deepseek_runtime_cache()
    initialize_database(database_path())
    catchup_task: asyncio.Task[None] | None = None
    strategy_task: asyncio.Task[None] | None = None
    if os.environ.get("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0") == "1":
        catchup_task = asyncio.create_task(_run_startup_catchup())
    if (
        os.environ.get(
            "FOURSEASQUANT_ENABLE_CORE_STRATEGY_AUTOMATION",
            "0",
        )
        == "1"
    ):
        strategy_task = asyncio.create_task(
            _run_core_strategy_automation_loop()
        )
    yield
    if strategy_task:
        strategy_task.cancel()
        with suppress(asyncio.CancelledError):
            await strategy_task
    if catchup_task:
        await catchup_task


async def _run_startup_catchup() -> None:
    try:
        await asyncio.to_thread(run_startup_catchup)
    except Exception:
        # 启动补跑失败已有任务日志；不能阻止网站读取最近成功结果。
        pass


async def _run_core_strategy_automation_loop() -> None:
    """网站常驻进程负责持续推进调查批次和模拟仓位。"""

    while True:
        try:
            current = datetime.now(BEIJING)
            path = database_path()
            latest_due_date = resolve_manual_target_date(
                current.date(),
                now=current,
                path=path,
            )
            target_date = await asyncio.to_thread(
                next_core_strategy_automation_date,
                path,
                latest_due_date=latest_due_date,
            )
            if target_date is not None:
                await asyncio.to_thread(
                    advance_core_strategy_automation,
                    path,
                    target_date=target_date,
                    now=current,
                    start_opinion_batch=batch_controller.start,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 运行层已记录可公开的失败摘要；后台循环必须保活等待下一次重试。
            pass
        await asyncio.sleep(30)


app = FastAPI(title=APPLICATION_NAME, lifespan=lifespan)
app.include_router(core_strategy_router)
app.include_router(industry_chain_router)
app.include_router(public_opinion_router)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("FOURSEASQUANT_WEB_ORIGIN", "http://127.0.0.1:5173")
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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
    "/api/market-environment",
    response_model=MarketEnvironmentSnapshot,
)
def market_environment(target_date: date) -> MarketEnvironmentSnapshot:
    try:
        return read_market_environment(
            database_path(),
            requested_date=target_date,
        )
    except MarketEnvironmentUnavailable as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    "/api/market-environment/history",
    response_model=MarketEnvironmentHistory,
)
def market_environment_history(
    end_date: date,
    limit: int = Query(default=60, ge=1, le=500),
) -> MarketEnvironmentHistory:
    return read_market_environment_history(
        database_path(),
        requested_end_date=end_date,
        limit=limit,
    )


@app.post(
    "/api/market-environment/refresh",
    response_model=MarketEnvironmentRefreshResult,
    status_code=201,
)
def refresh_market_environment_endpoint(
    request: DailyTaskRequest,
) -> MarketEnvironmentRefreshResult:
    try:
        return refresh_market_environment(
            database_path(),
            requested_date=request.target_date,
        )
    except MarketEnvironmentUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
    "/api/market-data/candles/status",
    response_model=CandleAvailability,
)
def candle_availability(target_date: date) -> CandleAvailability:
    return read_candle_availability(database_path(), target_date)


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
    sort_by: TechnicalScoreSortField = "structure_score",
) -> list[TechnicalScoreView]:
    return read_top_technical_scores(
        database_path(),
        requested_date=target_date,
        limit=limit,
        sort_by=sort_by,
    )


@app.get(
    "/api/technical-scores",
    response_model=TechnicalScorePage,
)
def technical_scores(
    target_date: date,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    search: str = Query(default="", max_length=60),
    board: Literal["main", "chinext", "star"] | None = None,
    sort_by: TechnicalScoreSortField = "structure_score",
    sort_order: TechnicalScoreSortOrder = "desc",
) -> TechnicalScorePage:
    return read_technical_score_page(
        database_path(),
        requested_date=target_date,
        page=page,
        page_size=page_size,
        search=search,
        board=board,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.get("/api/contracts/sector-membership")
def sector_membership_contract() -> dict[str, object]:
    return membership_json_schema()


@app.get(
    "/api/fundamentals/board-candidates/latest",
    response_model=BoardCandidatePublication,
)
def latest_fundamental_board_candidates(
    target_date: date | None = None,
) -> BoardCandidatePublication:
    publication = read_latest_board_candidate_publication(
        database_path(),
        target_date=target_date,
    )
    if publication is None:
        raise HTTPException(status_code=404, detail="尚无完整板块候选池快照")
    return publication


@app.get(
    "/api/fundamentals/board-candidates/{board_code}",
    response_model=BoardCandidate,
)
def fundamental_board_candidate(
    board_code: str,
    target_date: date | None = None,
) -> BoardCandidate:
    board = read_board_candidate(
        database_path(),
        board_code,
        target_date=target_date,
    )
    if board is None:
        raise HTTPException(status_code=404, detail="未找到候选板块")
    return board


@app.get(
    "/api/fundamentals/capital-actions/status",
    response_model=CapitalActionStatus,
)
def fundamental_capital_action_status(
    target_date: date,
) -> CapitalActionStatus:
    return read_capital_action_status(
        database_path(),
        target_date=target_date,
    )


@app.post(
    "/api/fundamentals/capital-actions/retry",
    response_model=FundamentalAutomationOutcome,
    status_code=201,
)
def retry_fundamental_capital_actions() -> FundamentalAutomationOutcome:
    return run_scheduled_fundamental_update(force=True)


@app.get(
    "/api/fundamentals/securities/{symbol}/capital-actions",
    response_model=CapitalActionEvidence,
)
def fundamental_capital_action_evidence(
    symbol: str,
    target_date: date | None = None,
) -> CapitalActionEvidence:
    try:
        evidence = read_capital_action_evidence(
            database_path(),
            symbol,
            target_date=target_date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if evidence is None:
        raise HTTPException(
            status_code=404,
            detail="该股票尚无已发布资本行为证据",
        )
    return evidence


@app.get(
    "/api/fundamentals/securities/{symbol}/monthly/latest",
    response_model=MonthlyFundamentalRecord,
)
def latest_monthly_fundamental(
    symbol: str,
    target_date: date | None = None,
) -> MonthlyFundamentalRecord:
    try:
        record = read_latest_monthly_fundamental(
            database_path(),
            symbol,
            target_date=target_date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="该股票尚无月度基本面快照")
    return record


@app.get(
    "/api/fundamentals/securities/{symbol}/monthly",
    response_model=MonthlyFundamentalSeries,
)
def monthly_fundamental_history(
    symbol: str,
    target_date: date | None = None,
    limit: int = Query(default=12, ge=1, le=120),
) -> MonthlyFundamentalSeries:
    try:
        return read_monthly_fundamental_series(
            database_path(),
            symbol,
            target_date=target_date,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get(
    "/api/fundamentals/securities/{symbol}/discussion/latest",
    response_model=DiscussionDayView,
)
def latest_fundamental_discussion(
    symbol: str,
    target_date: date | None = None,
) -> DiscussionDayView:
    try:
        result = read_latest_discussion(
            database_path(),
            symbol,
            target_date=target_date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="该股票尚无每日舆情快照")
    return result


@app.get(
    "/api/fundamentals/securities/{symbol}/discussion",
    response_model=DiscussionSeries,
)
def fundamental_discussion_history(
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=30, ge=1, le=365),
) -> DiscussionSeries:
    try:
        return read_discussion_series(
            database_path(),
            symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get(
    "/api/fundamentals/overview",
    response_model=FundamentalOverview,
)
def fundamental_overview(
    target_date: date | None = None,
    board_code: str | None = None,
    search: str = Query(default="", max_length=40),
    sort_by: OverviewSortField = "code",
    sort_order: SortOrder = "asc",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> FundamentalOverview:
    try:
        return read_fundamental_overview(
            database_path(),
            target_date=target_date,
            board_id=board_code,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get(
    "/api/fundamentals/lynch/overview",
    response_model=LynchMarketOverview,
)
def lynch_market_overview(
    target_date: date,
    search: str = Query(default="", max_length=40),
    sort_by: LynchSortField = "market_percentile",
    sort_order: SortOrder = "desc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LynchMarketOverview:
    try:
        return read_lynch_market_overview(
            database_path(),
            target_date=target_date,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post(
    "/api/fundamentals/lynch/retry",
    response_model=LynchAutomationOutcome,
    status_code=201,
)
def retry_lynch_market_update(request: DailyTaskRequest) -> LynchAutomationOutcome:
    return run_scheduled_lynch_update(
        target_date=request.target_date,
        force=True,
    )


@app.post("/api/tasks/daily", response_model=TaskRunResponse, status_code=201)
def run_daily_task(request: DailyTaskRequest) -> TaskRunResponse:
    return _run_claimed_manual_task(request.target_date, trigger_method="manual")


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
    return _run_claimed_manual_task(request.target_date, trigger_method="retry")


def _run_claimed_manual_task(
    target_date: date,
    *,
    trigger_method: Literal["manual", "retry"],
) -> TaskRunResponse:
    path = database_path()
    effective_target_date = resolve_manual_target_date(
        target_date,
        path=path,
    )
    lease = TargetDateClaimLease.acquire(
        path,
        effective_target_date,
    )
    if lease is None:
        raise HTTPException(status_code=409, detail="该目标日期已有任务正在运行")
    with lease:
        if os.environ.get("FOURSEASQUANT_ENABLE_FAILURE_SIMULATION") == "1":
            return execute_daily_task(
                effective_target_date,
                trigger_method=trigger_method,
            )
        return execute_daily_task_with_candles(
            effective_target_date,
            trigger_method=trigger_method,
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
    def frontend_page() -> FileResponse:
        return FileResponse(FRONTEND_DISTRIBUTION / "index.html")

    for frontend_route in (
        "/overview",
        "/market",
        "/market-environment",
        "/quotes",
        "/technical-scores",
        "/strategy",
        "/fundamentals",
        "/public-opinion",
        "/industry-chain-leaders",
        "/tasks",
    ):
        app.add_api_route(
            frontend_route,
            frontend_page,
            methods=["GET"],
            include_in_schema=False,
        )

    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DISTRIBUTION, html=True),
        name="frontend",
    )
