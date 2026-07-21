from __future__ import annotations

from datetime import date

import pytest

from fourseasquant.strategy_performance import (
    DailyReturn,
    calculate_strategy_performance,
)


def test_strategy_calculation_uses_compounded_nav_and_peak_drawdown() -> None:
    performance = calculate_strategy_performance(
        [
            DailyReturn(date=date(2026, 7, 20), strategy_return=0.10, benchmark_return=0.0),
            DailyReturn(date=date(2026, 7, 21), strategy_return=-0.10, benchmark_return=0.0),
        ],
        benchmark_label="沪深 300",
    )

    assert performance.points[0].strategy_nav == pytest.approx(1.10)
    assert performance.points[1].strategy_nav == pytest.approx(0.99)
    assert performance.points[1].excess_nav == pytest.approx(0.99)
    assert performance.points[1].drawdown_pct == pytest.approx(-10.0)
    assert performance.statistics.cumulative_return_pct == pytest.approx(-1.0)
    assert performance.statistics.max_drawdown_pct == pytest.approx(-10.0)
    assert performance.statistics.current_drawdown_pct == pytest.approx(-10.0)
    assert performance.statistics.win_rate_pct == pytest.approx(50.0)
