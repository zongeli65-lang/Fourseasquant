from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from fourseasquant.akshare_history import HISTORY_SOURCE
from fourseasquant.sqlite_connection import open_database_connection


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


class MarketActivityView(BaseModel):
    limit_up: int
    limit_down: int
    consecutive_limit_up: int
    new_high_20d: int
    new_low_20d: int


OverviewCategory = Literal[
    "eligible",
    "advancers",
    "decliners",
    "unchanged",
    "turnover",
    "limit_up",
    "limit_down",
    "consecutive_limit_up",
    "new_high_20d",
    "new_low_20d",
]


class OverviewSecurityView(SecurityMarketView):
    status: list[str]
    sector_labels: list[str] = Field(default_factory=list)
    concept_labels: list[str] = Field(default_factory=list)


class OverviewSecurityPage(BaseModel):
    requested_date: date
    actual_data_date: date
    category: OverviewCategory
    category_label: str
    total: int
    page: int
    page_size: int
    items: list[OverviewSecurityView]


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
    activity: MarketActivityView
    distribution: list[DistributionBucket]
    gainers: list[SecurityMarketView]
    losers: list[SecurityMarketView]
    heatmap: list[SecurityMarketView]
    trend: list[RealMarketTrendPoint]


def read_real_market_dashboard(
    path: Path, requested_date: date
) -> RealMarketDashboard:
    actual_data_date = _latest_actual_data_date(path, requested_date)
    with open_database_connection(path) as connection:
        trend_rows = connection.execute(
            """
            SELECT facts.actual_data_date,
                   benchmark.close,
                   SUM(facts.turnover_cny),
                   COUNT(*),
                   SUM(CASE WHEN facts.change_pct > 0 THEN 1 ELSE 0 END)
            FROM historical_security_facts AS facts
            JOIN historical_benchmark_facts AS benchmark
              ON benchmark.source = facts.source
             AND benchmark.actual_data_date = facts.actual_data_date
            WHERE facts.source = ?
              AND facts.actual_data_date <= ?
              AND facts.listing_trading_days >= 60
              AND facts.code NOT LIKE '30%'
              AND facts.code NOT LIKE '68%'
            GROUP BY facts.actual_data_date, benchmark.close
            ORDER BY facts.actual_data_date
            """,
            (HISTORY_SOURCE, actual_data_date.isoformat()),
        ).fetchall()
        selected_rows = connection.execute(
            """
            SELECT code, name, close, change_pct, turnover_cny
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
              AND listing_trading_days >= 60
              AND code NOT LIKE '30%'
              AND code NOT LIKE '68%'
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

    securities = [_security_view(row) for row in selected_rows]
    classifications = _classify_securities(path, actual_data_date, securities)
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
        activity=MarketActivityView(
            limit_up=sum(item.limit_up for item in classifications.values()),
            limit_down=sum(item.limit_down for item in classifications.values()),
            consecutive_limit_up=sum(
                item.limit_up_streak >= 2 for item in classifications.values()
            ),
            new_high_20d=sum(
                item.new_high_20d for item in classifications.values()
            ),
            new_low_20d=sum(
                item.new_low_20d for item in classifications.values()
            ),
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


CATEGORY_LABELS: dict[OverviewCategory, str] = {
    "eligible": "有效样本",
    "advancers": "上涨股票",
    "decliners": "下跌股票",
    "unchanged": "平盘股票",
    "turnover": "成交额明细",
    "limit_up": "涨停股票",
    "limit_down": "跌停股票",
    "consecutive_limit_up": "连板股票",
    "new_high_20d": "20 日新高",
    "new_low_20d": "20 日新低",
}


class _SecurityClassification(BaseModel):
    limit_up: bool = False
    limit_down: bool = False
    limit_up_streak: int = 0
    new_high_20d: bool = False
    new_low_20d: bool = False


def read_overview_securities(
    path: Path,
    *,
    requested_date: date,
    category: OverviewCategory,
    page: int = 1,
    page_size: int = 50,
    query: str = "",
) -> OverviewSecurityPage:
    actual_date = _latest_actual_data_date(path, requested_date)
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code, name, close, change_pct, turnover_cny
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
              AND listing_trading_days >= 60
              AND code NOT LIKE '30%'
              AND code NOT LIKE '68%'
            ORDER BY code
            """,
            (HISTORY_SOURCE, actual_date.isoformat()),
        ).fetchall()
    securities = [_security_view(row) for row in rows]
    classifications = _classify_securities(path, actual_date, securities)
    normalized_query = query.strip().casefold()
    filtered = [
        security
        for security in securities
        if _matches_category(security, classifications[security.code], category)
        and (
            not normalized_query
            or normalized_query in security.code.casefold()
            or normalized_query in security.name.casefold()
        )
    ]
    filtered.sort(
        key=lambda item: _category_sort_key(
            item, classifications[item.code], category
        )
    )
    total = len(filtered)
    safe_page_size = max(1, min(page_size, 100))
    safe_page = max(1, page)
    start = (safe_page - 1) * safe_page_size
    items = [
        OverviewSecurityView(
            **security.model_dump(),
            status=_status_labels(security, classifications[security.code]),
        )
        for security in filtered[start : start + safe_page_size]
    ]
    return OverviewSecurityPage(
        requested_date=requested_date,
        actual_data_date=actual_date,
        category=category,
        category_label=CATEGORY_LABELS[category],
        total=total,
        page=safe_page,
        page_size=safe_page_size,
        items=items,
    )


def _latest_actual_data_date(path: Path, requested_date: date) -> date:
    with open_database_connection(path) as connection:
        row = cast(
            tuple[str] | None,
            connection.execute(
                """
                SELECT MAX(actual_data_date)
                FROM historical_market_daily_summary
                WHERE source = ? AND actual_data_date <= ?
                """,
                (HISTORY_SOURCE, requested_date.isoformat()),
            ).fetchone(),
        )
    if row is None or row[0] is None:
        raise RealMarketDataNotFound("目标日期前没有完整发布的 AKShare 真实日频数据")
    return date.fromisoformat(row[0])


def _security_view(row: tuple[object, ...]) -> SecurityMarketView:
    return SecurityMarketView(
        code=cast(str, row[0]),
        name=cast(str, row[1]),
        close=cast(float, row[2]),
        change_pct=cast(float, row[3]),
        turnover_cny=cast(int, row[4]),
    )


def _classify_securities(
    path: Path,
    actual_date: date,
    securities: list[SecurityMarketView],
) -> dict[str, _SecurityClassification]:
    codes = {security.code for security in securities}
    with open_database_connection(path) as connection:
        date_rows = connection.execute(
            """
            SELECT DISTINCT actual_data_date
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date <= ?
            ORDER BY actual_data_date DESC
            LIMIT 20
            """,
            (HISTORY_SOURCE, actual_date.isoformat()),
        ).fetchall()
        if not date_rows:
            return {code: _SecurityClassification() for code in codes}
        history_start = cast(str, date_rows[-1][0])
        history_rows = connection.execute(
            """
            SELECT actual_data_date, code, close, previous_close
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date BETWEEN ? AND ?
            ORDER BY code, actual_data_date
            """,
            (HISTORY_SOURCE, history_start, actual_date.isoformat()),
        ).fetchall()
    histories: dict[str, list[tuple[str, float, float]]] = {code: [] for code in codes}
    for row in history_rows:
        code = cast(str, row[1])
        if code in histories:
            histories[code].append(
                (cast(str, row[0]), cast(float, row[2]), cast(float, row[3]))
            )
    result: dict[str, _SecurityClassification] = {}
    for code, rows in histories.items():
        if not rows:
            result[code] = _SecurityClassification()
            continue
        latest = rows[-1]
        closes = [row[1] for row in rows]
        streak = 0
        for _, close, previous_close in reversed(rows):
            if not _at_price_limit(close, previous_close, 1):
                break
            streak += 1
        has_20_days = len(closes) >= 20
        result[code] = _SecurityClassification(
            limit_up=_at_price_limit(latest[1], latest[2], 1),
            limit_down=_at_price_limit(latest[1], latest[2], -1),
            limit_up_streak=streak,
            new_high_20d=has_20_days and latest[1] >= max(closes),
            new_low_20d=has_20_days and latest[1] <= min(closes),
        )
    return result


def _at_price_limit(close: float, previous_close: float, direction: int) -> bool:
    if previous_close <= 0:
        return False
    multiplier = Decimal("1.10") if direction > 0 else Decimal("0.90")
    limit_price = (Decimal(str(previous_close)) * multiplier).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    close_price = Decimal(str(close)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return close_price == limit_price


def _matches_category(
    security: SecurityMarketView,
    classification: _SecurityClassification,
    category: OverviewCategory,
) -> bool:
    if category in ("eligible", "turnover"):
        return True
    if category == "advancers":
        return security.change_pct > 0
    if category == "decliners":
        return security.change_pct < 0
    if category == "unchanged":
        return security.change_pct == 0
    if category == "limit_up":
        return classification.limit_up
    if category == "limit_down":
        return classification.limit_down
    if category == "consecutive_limit_up":
        return classification.limit_up_streak >= 2
    if category == "new_high_20d":
        return classification.new_high_20d
    return classification.new_low_20d


def _category_sort_key(
    security: SecurityMarketView,
    classification: _SecurityClassification,
    category: OverviewCategory,
) -> tuple[float, float, str]:
    if category in ("decliners", "limit_down", "new_low_20d"):
        return (security.change_pct, -security.turnover_cny, security.code)
    if category == "consecutive_limit_up":
        return (-classification.limit_up_streak, -security.turnover_cny, security.code)
    if category in ("eligible", "turnover", "unchanged"):
        return (-security.turnover_cny, -security.change_pct, security.code)
    return (-security.change_pct, -security.turnover_cny, security.code)


def _status_labels(
    security: SecurityMarketView,
    classification: _SecurityClassification,
) -> list[str]:
    labels: list[str] = []
    if classification.limit_up_streak >= 2:
        labels.append(f"{classification.limit_up_streak} 连板")
    elif classification.limit_up:
        labels.append("涨停")
    if classification.limit_down:
        labels.append("跌停")
    if classification.new_high_20d:
        labels.append("20 日新高")
    if classification.new_low_20d:
        labels.append("20 日新低")
    if not labels:
        labels.append("上涨" if security.change_pct > 0 else "下跌" if security.change_pct < 0 else "平盘")
    return labels


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
