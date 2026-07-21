from __future__ import annotations

import math
from datetime import date

from fourseasquant.strategy_performance import (
    DailyReturn,
    StrategyPerformance,
    calculate_strategy_performance,
)
from fourseasquant.trading_calendar import trading_days_between


def simulated_strategy_performance(
    target_date: date,
    benchmark_label: str = "沪深 300",
    *,
    strategy_return_scale: float = 1.0,
) -> StrategyPerformance:
    try:
        start_date = target_date.replace(year=target_date.year - 3)
    except ValueError:
        start_date = target_date.replace(year=target_date.year - 3, day=28)

    daily_returns = [
        DailyReturn(
            date=trading_date,
            strategy_return=strategy_return_scale * (
                0.00045
                + math.sin(index * 0.21) * 0.0045
                + math.cos(index * 0.071) * 0.002
            ),
            benchmark_return=_benchmark_return(index, benchmark_label),
        )
        for index, trading_date in enumerate(
            trading_days_between(start_date, target_date)
        )
    ]
    return calculate_strategy_performance(
        daily_returns,
        benchmark_label=benchmark_label,
    )


def _benchmark_return(index: int, benchmark_label: str) -> float:
    if benchmark_label == "中证 500":
        return (
            0.00018
            + math.sin(index * 0.23) * 0.0042
            + math.cos(index * 0.061) * 0.0018
        )
    return (
        0.00025
        + math.sin(index * 0.19) * 0.0035
        + math.cos(index * 0.083) * 0.0015
    )
