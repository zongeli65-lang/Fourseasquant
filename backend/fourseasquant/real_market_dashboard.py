from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from fourseasquant.akshare_history import HISTORY_SOURCE


class RealMarketDataNotFound(LookupError):
    pass


class BenchmarkView(BaseModel):
    name: str
    close: float
    change_pct: float


class BreadthView(BaseModel):
    advancers: int
    decliners: int
    unchanged: int
    advancer_ratio: float


class DistributionBucket(BaseModel):
    label: str
    count: int
    tone: str


class SecurityMarketView(BaseModel):
    code: str
    name: str
    close: float
    change_pct: float
    turnover_cny: int


class RealMarketTrendPoint(BaseModel):
    date: date
    benchmark_close: float
    turnover_cny: int
    advancer_ratio: float


class RealMarketDashboard(BaseModel):
    source: str
    requested_date: date
    actual_data_date: date
    coverage_start: date
    coverage_end: date
    benchmark: BenchmarkView
    eligible_security_count: int
    turnover_cny: int
    turnover_change_vs_20d_pct: float
    breadth: BreadthView
    distribution: list[DistributionBucket]
    gainers: list[SecurityMarketView]
    losers: list[SecurityMarketView]
    heatmap: list[SecurityMarketView]
    trend: list[RealMarketTrendPoint]


def read_real_market_dashboard(
    path: Path, requested_date: date
) -> RealMarketDashboard:
    with sqlite3.connect(path) as connection:
        actual_row = cast(
            tuple[str] | None,
            connection.execute(
                """
                SELECT MAX(actual_data_date)
                FROM historical_security_facts
                WHERE source = ? AND actual_data_date <= ?
                """,
                (HISTORY_SOURCE, requested_date.isoformat()),
            ).fetchone(),
        )
        if actual_row is None or actual_row[0] is None:
            raise RealMarketDataNotFound("目标日期前没有 AKShare 真实日频数据")
        actual_data_date = date.fromisoformat(actual_row[0])

        trend_rows = connection.execute(
            """
            SELECT actual_data_date,
                   benchmark_close,
                   turnover_cny,
                   security_count,
                   advancers
            FROM historical_market_daily_summary
            WHERE source = ? AND actual_data_date <= ?
            ORDER BY actual_data_date
            """,
            (HISTORY_SOURCE, actual_data_date.isoformat()),
        ).fetchall()
        selected_rows = connection.execute(
            """
            SELECT code, name, close, change_pct, turnover_cny
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            ORDER BY code
            """,
            (HISTORY_SOURCE, actual_data_date.isoformat()),
        ).fetchall()
        benchmark_row = cast(
            tuple[str, float] | None,
            connection.execute(
                """
                SELECT name, close
                FROM historical_benchmark_facts
                WHERE source = ? AND actual_data_date = ?
                """,
                (HISTORY_SOURCE, actual_data_date.isoformat()),
            ).fetchone(),
        )

    if not trend_rows or not selected_rows or benchmark_row is None:
        raise RealMarketDataNotFound("真实行情缓存尚未形成可视化聚合数据")

    securities = [
        SecurityMarketView(
            code=cast(str, row[0]),
            name=cast(str, row[1]),
            close=cast(float, row[2]),
            change_pct=cast(float, row[3]),
            turnover_cny=cast(int, row[4]),
        )
        for row in selected_rows
    ]
    advancers = sum(security.change_pct > 0 for security in securities)
    decliners = sum(security.change_pct < 0 for security in securities)
    unchanged = len(securities) - advancers - decliners
    turnover_cny = sum(security.turnover_cny for security in securities)
    previous_turnovers = [cast(int, row[2]) for row in trend_rows[-21:-1]]
    previous_average = (
        sum(previous_turnovers) / len(previous_turnovers)
        if previous_turnovers
        else turnover_cny
    )
    previous_benchmark_close = (
        cast(float, trend_rows[-2][1]) if len(trend_rows) > 1 else benchmark_row[1]
    )
    trend = [
        RealMarketTrendPoint(
            date=date.fromisoformat(cast(str, row[0])),
            benchmark_close=cast(float, row[1]),
            turnover_cny=cast(int, row[2]),
            advancer_ratio=(cast(int, row[4]) / cast(int, row[3])) * 100,
        )
        for row in trend_rows
    ]

    return RealMarketDashboard(
        source="akshare",
        requested_date=requested_date,
        actual_data_date=actual_data_date,
        coverage_start=trend[0].date,
        coverage_end=trend[-1].date,
        benchmark=BenchmarkView(
            name=benchmark_row[0],
            close=benchmark_row[1],
            change_pct=(benchmark_row[1] / previous_benchmark_close - 1) * 100,
        ),
        eligible_security_count=len(securities),
        turnover_cny=turnover_cny,
        turnover_change_vs_20d_pct=(turnover_cny / previous_average - 1) * 100,
        breadth=BreadthView(
            advancers=advancers,
            decliners=decliners,
            unchanged=unchanged,
            advancer_ratio=advancers / len(securities) * 100,
        ),
        distribution=_distribution(securities),
        gainers=sorted(
            securities, key=lambda security: security.change_pct, reverse=True
        )[:10],
        losers=sorted(securities, key=lambda security: security.change_pct)[:10],
        heatmap=sorted(
            securities, key=lambda security: security.turnover_cny, reverse=True
        )[:100],
        trend=trend,
    )


def _distribution(
    securities: list[SecurityMarketView],
) -> list[DistributionBucket]:
    definitions: list[tuple[str, Callable[[float], bool], str]] = [
        ("≤ -7%", lambda value: value <= -7, "negative-strong"),
        ("-7% ～ -3%", lambda value: -7 < value <= -3, "negative"),
        ("-3% ～ 0%", lambda value: -3 < value < 0, "negative-light"),
        ("0%", lambda value: value == 0, "neutral"),
        ("0% ～ 3%", lambda value: 0 < value < 3, "positive-light"),
        ("3% ～ 7%", lambda value: 3 <= value < 7, "positive"),
        ("≥ 7%", lambda value: value >= 7, "positive-strong"),
    ]
    return [
        DistributionBucket(
            label=label,
            count=sum(predicate(security.change_pct) for security in securities),
            tone=tone,
        )
        for label, predicate, tone in definitions
    ]
