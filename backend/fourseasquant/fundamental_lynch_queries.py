from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from fourseasquant.fundamental_lynch import LynchDailyResult
from fourseasquant.fundamental_lynch_repository import (
    read_latest_published_lynch_daily_batch,
)


LynchSortField = Literal["market_percentile", "lynch_ratio", "code"]
SortOrder = Literal["asc", "desc"]


class LynchMarketOverview(BaseModel):
    requested_date: date
    actual_data_date: date
    financial_base_date: date
    financial_base_age_days: int
    financial_base_status: Literal["fresh", "warning", "expired"]
    published_at: datetime
    rules_version: str
    total_count: int
    calculable_count: int
    ranking_eligible_count: int
    unavailable_count: int
    audit_pending_count: int
    offset: int
    limit: int
    items: list[LynchDailyResult]


def read_lynch_market_overview(
    path: Path,
    *,
    target_date: date,
    search: str = "",
    sort_by: LynchSortField = "market_percentile",
    sort_order: SortOrder = "desc",
    limit: int = 100,
    offset: int = 0,
) -> LynchMarketOverview:
    publication = read_latest_published_lynch_daily_batch(path, target_date)
    if publication is None:
        raise LookupError("尚无已完整发布的全市场林奇日频结果")
    all_items = list(publication.results.values())
    normalized_search = search.strip().upper()
    filtered = [
        item
        for item in all_items
        if not normalized_search
        or normalized_search in item.code
        or normalized_search in item.name.upper()
    ]
    if sort_by == "code":
        filtered.sort(
            key=lambda item: item.code,
            reverse=sort_order == "desc",
        )
    else:
        available = [
            item
            for item in filtered
            if _numeric_sort_value(item, sort_by) is not None
        ]
        missing = [
            item
            for item in filtered
            if _numeric_sort_value(item, sort_by) is None
        ]
        available.sort(
            key=lambda item: (
                _numeric_sort_value(item, sort_by) or 0.0,
                item.code,
            ),
            reverse=sort_order == "desc",
        )
        missing.sort(key=lambda item: item.code)
        filtered = available + missing
    age = (
        publication.target_date - publication.financial_base_date
    ).days
    base_status: Literal["fresh", "warning", "expired"]
    if age > 45:
        base_status = "expired"
    elif age >= 32:
        base_status = "warning"
    else:
        base_status = "fresh"
    return LynchMarketOverview(
        requested_date=target_date,
        actual_data_date=publication.target_date,
        financial_base_date=publication.financial_base_date,
        financial_base_age_days=age,
        financial_base_status=base_status,
        published_at=publication.published_at,
        rules_version=publication.rules_version,
        total_count=len(all_items),
        calculable_count=sum(item.calculable for item in all_items),
        ranking_eligible_count=sum(
            item.ranking_eligible for item in all_items
        ),
        unavailable_count=sum(not item.calculable for item in all_items),
        audit_pending_count=sum(
            item.audit_status == "unknown" for item in all_items
        ),
        offset=offset,
        limit=limit,
        items=filtered[offset : offset + limit],
    )


def _numeric_sort_value(
    item: LynchDailyResult,
    sort_by: LynchSortField,
) -> float | None:
    return (
        item.market_percentile
        if sort_by == "market_percentile"
        else item.lynch_ratio
    )
