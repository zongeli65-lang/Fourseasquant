from __future__ import annotations

import math
from datetime import date

from fourseasquant.strategy_performance import (
    DailyReturn,
    StrategyPerformance,
    calculate_strategy_performance,
)
from fourseasquant.trading_calendar import trading_days_between


def simulated_strategy_performance(target_date: date) -> StrategyPerformance:
    try:
        start_date = target_date.replace(year=target_date.year - 3)
    except ValueError:
        start_date = target_date.replace(year=target_date.year - 3, day=28)

    daily_returns = [
        DailyReturn(
            date=trading_date,
            strategy_return=(
                0.00045
                + math.sin(index * 0.21) * 0.0045
                + math.cos(index * 0.071) * 0.002
            ),
            benchmark_return=(
                0.00025
                + math.sin(index * 0.19) * 0.0035
                + math.cos(index * 0.083) * 0.0015
            ),
        )
        for index, trading_date in enumerate(
            trading_days_between(start_date, target_date)
        )
    ]
    return calculate_strategy_performance(
        daily_returns,
        benchmark_label="沪深 300",
    )
