from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, stdev
from typing import cast

from pydantic import BaseModel

from fourseasquant.core_strategy_repository import (
    CoreStrategyPortfolioSnapshot,
    create_core_strategy_tables,
)
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.trading_calendar import TRADING_DAYS_PER_YEAR


class CoreStrategyPerformancePoint(BaseModel):
    actual_date: date
    net_asset_value: float
    normalized_nav: float
    daily_profit_loss: float
    daily_return_pct: float
    cumulative_profit_loss: float
    cumulative_return_pct: float
    drawdown_pct: float


class CoreStrategyPerformanceStatistics(BaseModel):
    annualized_return_pct: float | None
    annualized_return_unavailable_reason: str | None
    max_drawdown_pct: float
    current_drawdown_pct: float
    sharpe_ratio: float | None
    sharpe_ratio_unavailable_reason: str | None
    win_rate_pct: float | None
    win_rate_unavailable_reason: str | None


class CoreStrategyPerformance(BaseModel):
    requested_as_of_date: date
    actual_date: date
    strategy_version: str
    sample_count: int
    initial_capital: float
    available_cash: float
    holding_market_value: float
    net_asset_value: float
    daily_profit_loss: float
    daily_return_pct: float
    cumulative_profit_loss: float
    cumulative_return_pct: float
    statistics: CoreStrategyPerformanceStatistics
    points: list[CoreStrategyPerformancePoint]


class _PublishedPortfolio(BaseModel):
    snapshot: CoreStrategyPortfolioSnapshot
    published_at: datetime


def read_core_strategy_performance(
    path: Path,
    *,
    as_of_date: date,
) -> CoreStrategyPerformance | None:
    """从真实组合快照计算不晚于指定日期的连续绩效。"""

    history = _read_portfolio_history(path, as_of_date=as_of_date)
    if not history:
        return None

    first = history[0].snapshot
    latest = history[-1].snapshot
    initial_capital = first.initial_capital
    previous_nav = initial_capital
    peak_nav = initial_capital
    daily_returns: list[float] = []
    points: list[CoreStrategyPerformancePoint] = []

    for published in history:
        snapshot = published.snapshot
        daily_profit_loss = snapshot.net_asset_value - previous_nav
        daily_return = snapshot.net_asset_value / previous_nav - 1
        cumulative_profit_loss = (
            snapshot.net_asset_value - initial_capital
        )
        cumulative_return = (
            snapshot.net_asset_value / initial_capital - 1
        )
        peak_nav = max(peak_nav, snapshot.net_asset_value)
        drawdown = snapshot.net_asset_value / peak_nav - 1
        daily_returns.append(daily_return)
        points.append(
            CoreStrategyPerformancePoint(
                actual_date=snapshot.actual_date,
                net_asset_value=round(snapshot.net_asset_value, 2),
                normalized_nav=round(
                    snapshot.net_asset_value / initial_capital,
                    6,
                ),
                daily_profit_loss=round(daily_profit_loss, 2),
                daily_return_pct=round(daily_return * 100, 4),
                cumulative_profit_loss=round(
                    cumulative_profit_loss,
                    2,
                ),
                cumulative_return_pct=round(
                    cumulative_return * 100,
                    4,
                ),
                drawdown_pct=round(drawdown * 100, 4),
            )
        )
        previous_nav = snapshot.net_asset_value

    latest_point = points[-1]
    statistics = _statistics(
        daily_returns,
        points=points,
        normalized_nav=latest.net_asset_value / initial_capital,
    )
    return CoreStrategyPerformance(
        requested_as_of_date=as_of_date,
        actual_date=latest.actual_date,
        strategy_version=latest.strategy_version,
        sample_count=len(history),
        initial_capital=round(initial_capital, 2),
        available_cash=round(latest.available_cash, 2),
        holding_market_value=round(
            latest.net_asset_value - latest.available_cash,
            2,
        ),
        net_asset_value=round(latest.net_asset_value, 2),
        daily_profit_loss=latest_point.daily_profit_loss,
        daily_return_pct=latest_point.daily_return_pct,
        cumulative_profit_loss=latest_point.cumulative_profit_loss,
        cumulative_return_pct=latest_point.cumulative_return_pct,
        statistics=statistics,
        points=points,
    )


def _read_portfolio_history(
    path: Path,
    *,
    as_of_date: date,
) -> list[_PublishedPortfolio]:
    if not path.exists():
        return []
    with open_database_connection(path) as connection:
        create_core_strategy_tables(connection)
        rows = connection.execute(
            """
            SELECT payload_json, published_at
            FROM core_strategy_portfolio_snapshots
            WHERE actual_date <= ?
            ORDER BY actual_date ASC, published_at DESC,
                     strategy_version DESC
            """,
            (as_of_date.isoformat(),),
        ).fetchall()

    history: list[_PublishedPortfolio] = []
    seen_dates: set[date] = set()
    for row in rows:
        snapshot = CoreStrategyPortfolioSnapshot.model_validate_json(
            cast(str, row[0])
        )
        if snapshot.actual_date in seen_dates:
            continue
        seen_dates.add(snapshot.actual_date)
        history.append(
            _PublishedPortfolio(
                snapshot=snapshot,
                published_at=datetime.fromisoformat(cast(str, row[1])),
            )
        )
    return history


def _statistics(
    daily_returns: list[float],
    *,
    points: list[CoreStrategyPerformancePoint],
    normalized_nav: float,
) -> CoreStrategyPerformanceStatistics:
    sample_count = len(daily_returns)
    annualized_return_pct: float | None = None
    annualized_reason: str | None = (
        "至少需要 2 个真实日频组合快照才能计算年化收益率"
    )
    win_rate_pct: float | None = None
    win_rate_reason: str | None = (
        "至少需要 2 个真实日频组合快照才能计算胜率"
    )
    sharpe_ratio: float | None = None
    sharpe_reason: str | None = (
        "至少需要 2 个真实日频组合快照才能计算夏普比率"
    )

    if sample_count >= 2:
        annualized_return_pct = round(
            (
                normalized_nav
                ** (TRADING_DAYS_PER_YEAR / sample_count)
                - 1
            )
            * 100,
            4,
        )
        annualized_reason = None
        win_rate_pct = round(
            sum(value > 0 for value in daily_returns)
            / sample_count
            * 100,
            4,
        )
        win_rate_reason = None
        volatility = stdev(daily_returns)
        if volatility > 0:
            sharpe_ratio = round(
                fmean(daily_returns)
                / volatility
                * math.sqrt(TRADING_DAYS_PER_YEAR),
                4,
            )
            sharpe_reason = None
        else:
            sharpe_reason = "日收益率没有变化，无法计算夏普比率"

    return CoreStrategyPerformanceStatistics(
        annualized_return_pct=annualized_return_pct,
        annualized_return_unavailable_reason=annualized_reason,
        max_drawdown_pct=min(point.drawdown_pct for point in points),
        current_drawdown_pct=points[-1].drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        sharpe_ratio_unavailable_reason=sharpe_reason,
        win_rate_pct=win_rate_pct,
        win_rate_unavailable_reason=win_rate_reason,
    )
