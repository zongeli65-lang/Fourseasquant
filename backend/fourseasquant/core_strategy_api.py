from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fourseasquant.core_strategy_performance import (
    CoreStrategyPerformance,
    read_core_strategy_performance,
)
from fourseasquant.core_strategy_repository import (
    PublishedCoreStrategyDaySnapshot,
    PublishedCoreStrategyPortfolioSnapshot,
    read_core_strategy_day,
    read_core_strategy_portfolio,
    read_latest_core_strategy_day,
    read_latest_core_strategy_portfolio,
)
from fourseasquant.core_strategy_runtime import (
    CoreStrategyAccount,
    CoreStrategyResetResult,
    CoreStrategyRunStatus,
    finalize_core_strategy_day,
    initialize_core_strategy_account,
    prepare_core_strategy_day,
    read_core_strategy_run_status,
    reset_core_strategy_account,
)
from fourseasquant.database import database_path


router = APIRouter(prefix="/api/core-strategy", tags=["核心交易策略"])
BEIJING = ZoneInfo("Asia/Shanghai")


class CoreStrategyAccountInitializeRequest(BaseModel):
    initial_capital: float = Field(gt=0)


class CoreStrategyRunRequest(BaseModel):
    target_date: date


class CoreStrategyAccountResetRequest(BaseModel):
    confirmation: str


@router.post(
    "/account",
    response_model=CoreStrategyAccount,
    status_code=201,
)
def initialize_account(
    request: CoreStrategyAccountInitializeRequest,
) -> CoreStrategyAccount:
    try:
        return initialize_core_strategy_account(
            database_path(),
            initial_capital=request.initial_capital,
            initialized_at=datetime.now(BEIJING),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete(
    "/account",
    response_model=CoreStrategyResetResult,
)
def reset_account(
    request: CoreStrategyAccountResetRequest,
) -> CoreStrategyResetResult:
    try:
        return reset_core_strategy_account(
            database_path(),
            reset_at=datetime.now(BEIJING),
            confirmation=request.confirmation,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/run-status",
    response_model=CoreStrategyRunStatus,
)
def get_core_strategy_run_status(
    target_date: date,
) -> CoreStrategyRunStatus:
    return read_core_strategy_run_status(
        database_path(),
        requested_date=target_date,
    )


@router.post(
    "/runs/prepare",
    response_model=CoreStrategyRunStatus,
)
def prepare_strategy_run(
    request: CoreStrategyRunRequest,
) -> CoreStrategyRunStatus:
    try:
        return prepare_core_strategy_day(
            database_path(),
            requested_date=request.target_date,
            prepared_at=datetime.now(BEIJING),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/runs/finalize",
    response_model=CoreStrategyRunStatus,
)
def finalize_strategy_run(
    request: CoreStrategyRunRequest,
) -> CoreStrategyRunStatus:
    try:
        return finalize_core_strategy_day(
            database_path(),
            actual_date=request.target_date,
            finalized_at=datetime.now(BEIJING),
        )
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/days/latest",
    response_model=PublishedCoreStrategyDaySnapshot,
)
def get_latest_core_strategy_day(
    as_of_date: date,
) -> PublishedCoreStrategyDaySnapshot:
    result = read_latest_core_strategy_day(
        database_path(),
        as_of_date=as_of_date,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="没有可用的策略日结果")
    return result


@router.get(
    "/days/{actual_date}",
    response_model=PublishedCoreStrategyDaySnapshot,
)
def get_core_strategy_day(
    actual_date: date,
    strategy_version: str | None = None,
) -> PublishedCoreStrategyDaySnapshot:
    try:
        result = read_core_strategy_day(
            database_path(),
            actual_date=actual_date,
            strategy_version=strategy_version,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="没有对应的策略日结果")
    return result


@router.get(
    "/portfolio/latest",
    response_model=PublishedCoreStrategyPortfolioSnapshot,
)
def get_latest_core_strategy_portfolio(
    as_of_date: date,
) -> PublishedCoreStrategyPortfolioSnapshot:
    result = read_latest_core_strategy_portfolio(
        database_path(),
        as_of_date=as_of_date,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="没有可用的完整组合状态")
    return result


@router.get(
    "/performance",
    response_model=CoreStrategyPerformance,
)
def get_core_strategy_performance(
    as_of_date: date,
) -> CoreStrategyPerformance:
    result = read_core_strategy_performance(
        database_path(),
        as_of_date=as_of_date,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="没有可用的真实组合绩效",
        )
    return result


@router.get(
    "/portfolio/{actual_date}",
    response_model=PublishedCoreStrategyPortfolioSnapshot,
)
def get_core_strategy_portfolio(
    actual_date: date,
    strategy_version: str | None = None,
) -> PublishedCoreStrategyPortfolioSnapshot:
    try:
        result = read_core_strategy_portfolio(
            database_path(),
            actual_date=actual_date,
            strategy_version=strategy_version,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="没有对应的完整组合状态")
    return result
