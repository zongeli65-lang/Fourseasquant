from __future__ import annotations

from fourseasquant.market_overview import (
    IndexMove,
    LimitStatus,
    MarketOverview,
    SecurityObservation,
    TradingBoard,
    calculate_market_overview,
)


def simulated_market_overview() -> MarketOverview:
    observations = [
        SecurityObservation(
            code="600001",
            board=TradingBoard.SHANGHAI_MAIN,
            change_pct=2.5,
            turnover_cny=100_000_000_000,
            average_turnover_20d_cny=90_000_000_000,
            is_new_high_20d=True,
        ),
        SecurityObservation(
            code="000001",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=-1.0,
            turnover_cny=80_000_000_000,
            average_turnover_20d_cny=70_000_000_000,
            is_new_low_20d=True,
        ),
        SecurityObservation(
            code="002001",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=0.0,
            turnover_cny=20_000_000_000,
            average_turnover_20d_cny=20_000_000_000,
        ),
        SecurityObservation(
            code="600002",
            board=TradingBoard.SHANGHAI_MAIN,
            change_pct=10.0,
            limit_status=LimitStatus.LIMIT_UP,
            limit_up_streak=3,
            turnover_cny=40_000_000_000,
            average_turnover_20d_cny=30_000_000_000,
            is_new_high_20d=True,
        ),
        SecurityObservation(
            code="000002",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=-10.0,
            limit_status=LimitStatus.LIMIT_DOWN,
            turnover_cny=30_000_000_000,
            average_turnover_20d_cny=30_000_000_000,
            is_new_low_20d=True,
        ),
        SecurityObservation(
            code="688001",
            board=TradingBoard.STAR,
            change_pct=20.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="300001",
            board=TradingBoard.CHINEXT,
            change_pct=-20.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="600003",
            board=TradingBoard.SHANGHAI_MAIN,
            is_st=True,
            change_pct=5.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="000003",
            board=TradingBoard.SHENZHEN_MAIN,
            is_suspended=True,
            change_pct=0.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="600004",
            board=TradingBoard.SHANGHAI_MAIN,
            is_delisting=True,
            change_pct=-10.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="000004",
            board=TradingBoard.SHENZHEN_MAIN,
            listing_trading_days=19,
            change_pct=10.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
    ]
    return calculate_market_overview(
        observations,
        [
            IndexMove(name="上证指数", change_pct=0.62),
            IndexMove(name="深证成指", change_pct=-0.31),
            IndexMove(name="创业板指", change_pct=0.18),
            IndexMove(name="沪深 300", change_pct=0.44),
        ],
    )
