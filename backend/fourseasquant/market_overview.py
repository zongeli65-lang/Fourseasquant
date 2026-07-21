from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TradingBoard(StrEnum):
    SHANGHAI_MAIN = "shanghai_main"
    SHENZHEN_MAIN = "shenzhen_main"
    STAR = "star"
    CHINEXT = "chinext"


class LimitStatus(StrEnum):
    NONE = "none"
    LIMIT_UP = "limit_up"
    LIMIT_DOWN = "limit_down"


class SecurityObservation(BaseModel):
    code: str
    board: TradingBoard
    is_st: bool = False
    is_suspended: bool = False
    is_delisting: bool = False
    listing_trading_days: int = Field(default=200, ge=0)
    change_pct: float
    limit_status: LimitStatus = LimitStatus.NONE
    limit_up_streak: int = Field(default=0, ge=0)
    turnover_cny: int = Field(ge=0)
    average_turnover_20d_cny: int = Field(ge=0)
    is_new_high_20d: bool = False
    is_new_low_20d: bool = False


class IndexMove(BaseModel):
    name: str
    change_pct: float


class MarketBreadth(BaseModel):
    advancers: int
    decliners: int
    unchanged: int
    advancer_ratio: float
    decliner_ratio: float
    unchanged_ratio: float


class LimitActivity(BaseModel):
    limit_up: int
    limit_down: int
    max_limit_up_streak: int


class MarketTurnover(BaseModel):
    amount_cny: int
    change_vs_20d_pct: float


class HighLowActivity(BaseModel):
    new_high_20d: int
    new_low_20d: int


class MarketOverview(BaseModel):
    indices: list[IndexMove]
    breadth: MarketBreadth
    limit_activity: LimitActivity
    turnover: MarketTurnover
    high_low: HighLowActivity
    eligible_security_count: int


def is_eligible_main_board_security(
    observation: SecurityObservation,
    new_stock_exclusion_days: int,
) -> bool:
    return (
        observation.board
        in {TradingBoard.SHANGHAI_MAIN, TradingBoard.SHENZHEN_MAIN}
        and not observation.is_st
        and not observation.is_suspended
        and not observation.is_delisting
        and observation.listing_trading_days >= new_stock_exclusion_days
    )


def calculate_market_overview(
    observations: list[SecurityObservation],
    indices: list[IndexMove],
    *,
    new_stock_exclusion_days: int = 20,
) -> MarketOverview:
    eligible = [
        observation
        for observation in observations
        if is_eligible_main_board_security(observation, new_stock_exclusion_days)
    ]
    security_count = len(eligible)
    advancers = sum(observation.change_pct > 0 for observation in eligible)
    decliners = sum(observation.change_pct < 0 for observation in eligible)
    unchanged = security_count - advancers - decliners

    def ratio(count: int) -> float:
        return round(count / security_count * 100, 2) if security_count else 0.0

    turnover = sum(observation.turnover_cny for observation in eligible)
    average_turnover = sum(
        observation.average_turnover_20d_cny for observation in eligible
    )
    turnover_change = (
        round((turnover / average_turnover - 1) * 100, 2)
        if average_turnover
        else 0.0
    )

    return MarketOverview(
        indices=indices,
        breadth=MarketBreadth(
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            advancer_ratio=ratio(advancers),
            decliner_ratio=ratio(decliners),
            unchanged_ratio=ratio(unchanged),
        ),
        limit_activity=LimitActivity(
            limit_up=sum(
                observation.limit_status is LimitStatus.LIMIT_UP
                for observation in eligible
            ),
            limit_down=sum(
                observation.limit_status is LimitStatus.LIMIT_DOWN
                for observation in eligible
            ),
            max_limit_up_streak=max(
                (
                    observation.limit_up_streak
                    for observation in eligible
                    if observation.limit_status is LimitStatus.LIMIT_UP
                ),
                default=0,
            ),
        ),
        turnover=MarketTurnover(
            amount_cny=turnover,
            change_vs_20d_pct=turnover_change,
        ),
        high_low=HighLowActivity(
            new_high_20d=sum(
                observation.is_new_high_20d for observation in eligible
            ),
            new_low_20d=sum(
                observation.is_new_low_20d for observation in eligible
            ),
        ),
        eligible_security_count=security_count,
    )
