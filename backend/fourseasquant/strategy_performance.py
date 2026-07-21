from __future__ import annotations

import math
from datetime import date
from statistics import fmean, stdev

from pydantic import BaseModel

from fourseasquant.trading_calendar import (
    TRADING_DAYS_PER_QUARTER,
    TRADING_DAYS_PER_YEAR,
)


class DailyReturn(BaseModel):
    date: date
    strategy_return: float
    benchmark_return: float


class PerformancePoint(BaseModel):
    date: date
    strategy_nav: float
    benchmark_nav: float
    excess_nav: float
    drawdown_pct: float


class DailyPerformanceSummary(BaseModel):
    strategy_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float


class PerformanceStatistics(BaseModel):
    cumulative_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    current_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float


class PerformanceRange(BaseModel):
    key: str
    label: str
    trading_day_count: int | None


class StrategyPerformance(BaseModel):
    is_demo: bool
    benchmark_label: str
    inception_date: date
    daily_summary: DailyPerformanceSummary
    statistics: PerformanceStatistics
    range_options: list[PerformanceRange]
    range_statistics: dict[str, PerformanceStatistics]
    points: list[PerformancePoint]


def calculate_strategy_performance(
    daily_returns: list[DailyReturn],
    *,
    benchmark_label: str,
) -> StrategyPerformance:
    if not daily_returns:
        raise ValueError("策略历史不能为空")

    points = _performance_points(daily_returns)
    statistics = _performance_statistics(daily_returns, points)
    range_options = [
        PerformanceRange(key="all", label="成立以来", trading_day_count=None),
        PerformanceRange(
            key="year",
            label="近 1 年",
            trading_day_count=TRADING_DAYS_PER_YEAR,
        ),
        PerformanceRange(
            key="quarter",
            label="近 3 月",
            trading_day_count=TRADING_DAYS_PER_QUARTER,
        ),
    ]
    range_statistics: dict[str, PerformanceStatistics] = {}
    for option in range_options:
        observations = (
            daily_returns[-option.trading_day_count :]
            if option.trading_day_count
            else daily_returns
        )
        option_points = _performance_points(observations)
        range_statistics[option.key] = _performance_statistics(
            observations,
            option_points,
        )

    last_return = daily_returns[-1]
    return StrategyPerformance(
        is_demo=True,
        benchmark_label=benchmark_label,
        inception_date=daily_returns[0].date,
        daily_summary=DailyPerformanceSummary(
            strategy_return_pct=round(last_return.strategy_return * 100, 4),
            benchmark_return_pct=round(last_return.benchmark_return * 100, 4),
            excess_return_pct=round(
                (last_return.strategy_return - last_return.benchmark_return) * 100,
                4,
            ),
        ),
        statistics=statistics,
        range_options=range_options,
        range_statistics=range_statistics,
        points=points,
    )


def _performance_points(daily_returns: list[DailyReturn]) -> list[PerformancePoint]:
    strategy_nav = 1.0
    benchmark_nav = 1.0
    strategy_peak = 1.0
    points: list[PerformancePoint] = []
    for observation in daily_returns:
        strategy_nav *= 1 + observation.strategy_return
        benchmark_nav *= 1 + observation.benchmark_return
        strategy_peak = max(strategy_peak, strategy_nav)
        points.append(
            PerformancePoint(
                date=observation.date,
                strategy_nav=round(strategy_nav, 6),
                benchmark_nav=round(benchmark_nav, 6),
                excess_nav=round(strategy_nav / benchmark_nav, 6),
                drawdown_pct=round((strategy_nav / strategy_peak - 1) * 100, 4),
            )
        )

    return points


def _performance_statistics(
    daily_returns: list[DailyReturn],
    points: list[PerformancePoint],
) -> PerformanceStatistics:
    returns = [observation.strategy_return for observation in daily_returns]
    volatility = stdev(returns) if len(returns) > 1 else 0.0
    strategy_nav = points[-1].strategy_nav
    annualized_return = (
        (strategy_nav ** (TRADING_DAYS_PER_YEAR / len(daily_returns)) - 1) * 100
        if strategy_nav > 0
        else -100.0
    )
    return PerformanceStatistics(
        cumulative_return_pct=round((strategy_nav - 1) * 100, 4),
        annualized_return_pct=round(annualized_return, 4),
        max_drawdown_pct=min(point.drawdown_pct for point in points),
        current_drawdown_pct=points[-1].drawdown_pct,
        sharpe_ratio=round(
            fmean(returns) / volatility * math.sqrt(TRADING_DAYS_PER_YEAR)
            if volatility
            else 0.0,
            4,
        ),
        win_rate_pct=round(
            sum(value > 0 for value in returns) / len(returns) * 100,
            4,
        ),
    )
