from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from fourseasquant.discussion_sentiment import (
    DiscussionPlatform,
    PlatformDiscussionAggregate,
)
from fourseasquant.fundamental_discovery import (
    BoardCandidate,
    BoardCandidateSnapshot,
)
from fourseasquant.fundamental_capital_actions import CapitalActionEvent
from fourseasquant.fundamental_mechanical import (
    PersonalFundamentalMonthlySnapshot,
    RULES_VERSION,
)
from fourseasquant.fundamental_lynch import LynchDailyResult
from fourseasquant.fundamental_lynch_repository import (
    read_latest_published_lynch_daily_batch,
)
from fourseasquant.fundamental_repository import (
    read_latest_capital_action_batch,
    read_latest_fundamental_update_attempt,
    read_latest_published_capital_action_snapshot,
)


FundamentalDataStatus = Literal["complete", "partial", "no_data"]
OverviewSortField = Literal[
    "code",
    "name",
    "as_of_date",
    "floating_market_cap",
    "floating_market_cap_percentile",
    "adjusted_pe",
    "lynch_growth_value_ratio",
    "lynch_ratio",
    "lynch_market_percentile",
    "true_money_signal_score",
    "discussion_date",
]
SortOrder = Literal["asc", "desc"]
CapitalActionDataStatus = Literal["ready", "stale", "failed", "unavailable"]


class BoardCandidatePublication(BaseModel):
    snapshot: BoardCandidateSnapshot
    collected_at: datetime


class MonthlyFundamentalRecord(BaseModel):
    snapshot: PersonalFundamentalMonthlySnapshot
    source_urls: list[str]
    created_at: datetime


class MonthlyFundamentalSeries(BaseModel):
    code: str
    records: list[MonthlyFundamentalRecord]


class CapitalActionEvidence(BaseModel):
    code: str
    as_of_date: date
    published_at: datetime
    events: list[CapitalActionEvent]


class CapitalActionStatus(BaseModel):
    requested_date: date
    publication_date: date | None
    published_at: datetime | None
    status: CapitalActionDataStatus
    expected_count: int
    completed_count: int
    event_count: int
    confirmed_event_count: int
    latest_attempt_date: date | None
    latest_attempt_at: datetime | None
    latest_attempt_status: Literal["published", "failed"] | None
    failure_stage: str | None
    errors: dict[str, str]


class DiscussionDayView(BaseModel):
    actual_date: date
    code: str
    eastmoney_guba: PlatformDiscussionAggregate | None
    xueqiu: PlatformDiscussionAggregate | None
    eastmoney_reference_count: int
    xueqiu_reference_count: int


class DiscussionSeries(BaseModel):
    code: str
    days: list[DiscussionDayView]


class FundamentalOverviewItem(BaseModel):
    code: str
    name: str
    board_ids: list[str]
    lynch: LynchDailyResult | None
    monthly: MonthlyFundamentalRecord | None
    discussion: DiscussionDayView | None
    data_status: FundamentalDataStatus


class FundamentalOverview(BaseModel):
    board_source: str | None
    board_effective_date: date | None
    board_collected_at: datetime | None
    board_complete: bool | None
    boards: list[BoardCandidate]
    selected_board_id: str | None
    lynch_actual_data_date: date | None
    lynch_financial_base_date: date | None
    lynch_published_at: datetime | None
    lynch_total_count: int
    lynch_calculable_count: int
    lynch_ranking_eligible_count: int
    total: int
    limit: int
    offset: int
    items: list[FundamentalOverviewItem]


def read_latest_board_candidate_publication(
    path: Path,
    *,
    source: str | None = None,
    target_date: date | None = None,
) -> BoardCandidatePublication | None:
    filters: list[str] = []
    parameters: list[object] = []
    if source is not None:
        filters.append("source = ?")
        parameters.append(source)
    if target_date is not None:
        filters.append("effective_date <= ?")
        parameters.append(target_date.isoformat())
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"""
            SELECT payload_json, collected_at
            FROM fundamental_board_candidate_snapshots
            {where_clause}
            ORDER BY effective_date DESC, collected_at DESC
            LIMIT 1
            """,
            tuple(parameters),
        ).fetchone()
    if row is None:
        return None
    return BoardCandidatePublication(
        snapshot=BoardCandidateSnapshot.model_validate_json(cast(str, row[0])),
        collected_at=datetime.fromisoformat(cast(str, row[1])),
    )


def read_board_candidate(
    path: Path,
    board_id: str,
    *,
    source: str | None = None,
    target_date: date | None = None,
) -> BoardCandidate | None:
    publication = read_latest_board_candidate_publication(
        path,
        source=source,
        target_date=target_date,
    )
    if publication is None:
        return None
    return next(
        (
            board
            for board in publication.snapshot.boards
            if board.board_id == board_id
            or board.source_board_code == board_id
        ),
        None,
    )


def read_monthly_fundamental_series(
    path: Path,
    code: str,
    *,
    target_date: date | None = None,
    limit: int = 12,
    rules_version: str = RULES_VERSION,
) -> MonthlyFundamentalSeries:
    _require_code(code)
    parameters: tuple[object, ...]
    date_filter = ""
    if target_date is None:
        parameters = (code, rules_version)
    else:
        date_filter = "AND as_of_date <= ?"
        parameters = (code, rules_version, target_date.isoformat())
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT as_of_date, rules_version, changed_fields_json,
                   source_urls_json, created_at
            FROM personal_fundamental_monthly_snapshots
            WHERE code = ? AND rules_version = ?
            {date_filter}
            ORDER BY as_of_date, created_at, rules_version
            """,
            parameters,
        ).fetchall()
    states: dict[str, dict[str, object]] = {}
    records: list[MonthlyFundamentalRecord] = []
    for (
        as_of_date,
        rules_version,
        changed_fields_json,
        source_urls_json,
        created_at,
    ) in rows:
        version = rules_version
        state = states.setdefault(
            version,
            {"rules_version": version, "code": code},
        )
        state["as_of_date"] = cast(str, as_of_date)
        state.update(
            cast(dict[str, object], json.loads(cast(str, changed_fields_json)))
        )
        records.append(
            MonthlyFundamentalRecord(
                snapshot=PersonalFundamentalMonthlySnapshot.model_validate(
                    dict(state)
                ),
                source_urls=cast(
                    list[str], json.loads(cast(str, source_urls_json))
                ),
                created_at=datetime.fromisoformat(cast(str, created_at)),
            )
        )
    records.sort(
        key=lambda item: (item.snapshot.as_of_date, item.created_at),
        reverse=True,
    )
    return MonthlyFundamentalSeries(code=code, records=records[:limit])


def read_latest_monthly_fundamental(
    path: Path,
    code: str,
    *,
    target_date: date | None = None,
) -> MonthlyFundamentalRecord | None:
    series = read_monthly_fundamental_series(
        path,
        code,
        target_date=target_date,
        limit=1,
    )
    return series.records[0] if series.records else None


def read_capital_action_evidence(
    path: Path,
    code: str,
    *,
    target_date: date | None = None,
) -> CapitalActionEvidence | None:
    _require_code(code)
    publication = read_latest_published_capital_action_snapshot(
        path,
        as_of_date=target_date,
    )
    if publication is None or code not in publication.completed_codes:
        return None
    return CapitalActionEvidence(
        code=code,
        as_of_date=publication.as_of_date,
        published_at=publication.published_at,
        events=[
            event for event in publication.events if event.code == code
        ],
    )


def read_capital_action_status(
    path: Path,
    *,
    target_date: date,
) -> CapitalActionStatus:
    latest_attempt = read_latest_capital_action_batch(
        path,
        as_of_date=target_date,
    )
    update_attempt = read_latest_fundamental_update_attempt(
        path,
        target_date=target_date,
    )
    publication = read_latest_published_capital_action_snapshot(
        path,
        as_of_date=target_date,
    )
    if publication is None:
        initial_failed = (
            latest_attempt is not None
            and latest_attempt.as_of_date == target_date
            and latest_attempt.status == "failed"
        )
        return CapitalActionStatus(
            requested_date=target_date,
            publication_date=None,
            published_at=None,
            status="failed" if initial_failed else "unavailable",
            expected_count=(
                len(latest_attempt.expected_codes)
                if latest_attempt is not None
                else 0
            ),
            completed_count=(
                len(latest_attempt.completed_codes)
                if latest_attempt is not None
                else 0
            ),
            event_count=0,
            confirmed_event_count=0,
            latest_attempt_date=(
                latest_attempt.as_of_date if latest_attempt else None
            ),
            latest_attempt_at=(
                latest_attempt.collected_at if latest_attempt else None
            ),
            latest_attempt_status=(
                latest_attempt.status if latest_attempt else None
            ),
            failure_stage=(
                "资本行为采集"
                if initial_failed
                else None
            ),
            errors=(
                latest_attempt.errors
                if initial_failed and latest_attempt is not None
                else {}
            ),
        )
    failed_today = (
        latest_attempt is not None
        and latest_attempt.as_of_date == target_date
        and latest_attempt.status == "failed"
    )
    fundamental_failed_today = (
        update_attempt is not None
        and update_attempt.target_date == target_date
        and update_attempt.status == "failed"
    )
    failure_attempted_at = (
        update_attempt.attempted_at
        if fundamental_failed_today and update_attempt is not None
        else (
            latest_attempt.collected_at
            if failed_today and latest_attempt is not None
            else None
        )
    )
    failure_stage = (
        "月度基本面快照"
        if fundamental_failed_today
        else ("资本行为采集" if failed_today else None)
    )
    failure_errors = (
        {"__global__": update_attempt.error_summary or "月度基本面失败"}
        if fundamental_failed_today and update_attempt is not None
        else (
            latest_attempt.errors
            if failed_today and latest_attempt is not None
            else {}
        )
    )
    return CapitalActionStatus(
        requested_date=target_date,
        publication_date=publication.as_of_date,
        published_at=publication.published_at,
        status=(
            "failed"
            if failed_today or fundamental_failed_today
            else (
                "ready"
                if publication.as_of_date == target_date
                else "stale"
            )
        ),
        expected_count=len(publication.expected_codes),
        completed_count=len(publication.completed_codes),
        event_count=len(publication.events),
        confirmed_event_count=sum(
            event.confirmed_for_score for event in publication.events
        ),
        latest_attempt_date=(
            target_date
            if failed_today or fundamental_failed_today
            else (latest_attempt.as_of_date if latest_attempt else None)
        ),
        latest_attempt_at=failure_attempted_at or (
            latest_attempt.collected_at if latest_attempt else None
        ),
        latest_attempt_status=(
            "failed"
            if failed_today or fundamental_failed_today
            else (latest_attempt.status if latest_attempt else None)
        ),
        failure_stage=failure_stage,
        errors=failure_errors,
    )


def read_discussion_series(
    path: Path,
    code: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 30,
) -> DiscussionSeries:
    _require_code(code)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("舆情开始日期不能晚于结束日期")
    where_parts = ["code = ?"]
    parameters: list[object] = [code]
    if start_date is not None:
        where_parts.append("actual_date >= ?")
        parameters.append(start_date.isoformat())
    if end_date is not None:
        where_parts.append("actual_date <= ?")
        parameters.append(end_date.isoformat())
    where_clause = " AND ".join(where_parts)
    with sqlite3.connect(path) as connection:
        aggregate_rows = connection.execute(
            f"""
            SELECT platform, actual_date, post_count, positive_count,
                   neutral_count, negative_count, raw_heat,
                   weighted_sentiment, heat_percentile, likes_missing
            FROM discussion_daily_aggregates
            WHERE {where_clause}
            ORDER BY actual_date DESC
            """,
            tuple(parameters),
        ).fetchall()
        reference_rows = connection.execute(
            f"""
            SELECT platform, actual_date, COUNT(*)
            FROM discussion_post_references
            WHERE {where_clause}
            GROUP BY platform, actual_date
            """,
            tuple(parameters),
        ).fetchall()

    platforms: dict[
        tuple[date, DiscussionPlatform], PlatformDiscussionAggregate
    ] = {}
    dates: set[date] = set()
    for row in aggregate_rows:
        platform = cast(DiscussionPlatform, row[0])
        actual_date = date.fromisoformat(cast(str, row[1]))
        dates.add(actual_date)
        platforms[(actual_date, platform)] = PlatformDiscussionAggregate(
            platform=platform,
            actual_date=actual_date,
            code=code,
            post_count=cast(int, row[2]),
            positive_count=cast(int, row[3]),
            neutral_count=cast(int, row[4]),
            negative_count=cast(int, row[5]),
            raw_heat=cast(float, row[6]),
            weighted_sentiment=cast(float, row[7]),
            heat_percentile=cast(float | None, row[8]),
            likes_missing=bool(row[9]),
        )
    reference_counts = {
        (
            date.fromisoformat(cast(str, row[1])),
            cast(DiscussionPlatform, row[0]),
        ): cast(int, row[2])
        for row in reference_rows
    }
    days = [
        DiscussionDayView(
            actual_date=actual_date,
            code=code,
            eastmoney_guba=platforms.get(
                (actual_date, "eastmoney_guba")
            ),
            xueqiu=platforms.get((actual_date, "xueqiu")),
            eastmoney_reference_count=reference_counts.get(
                (actual_date, "eastmoney_guba"), 0
            ),
            xueqiu_reference_count=reference_counts.get(
                (actual_date, "xueqiu"), 0
            ),
        )
        for actual_date in sorted(dates, reverse=True)[:limit]
    ]
    return DiscussionSeries(code=code, days=days)


def read_latest_discussion(
    path: Path,
    code: str,
    *,
    target_date: date | None = None,
) -> DiscussionDayView | None:
    series = read_discussion_series(
        path,
        code,
        end_date=target_date,
        limit=1,
    )
    return series.days[0] if series.days else None


def read_fundamental_overview(
    path: Path,
    *,
    target_date: date | None = None,
    board_id: str | None = None,
    search: str = "",
    sort_by: OverviewSortField = "code",
    sort_order: SortOrder = "asc",
    limit: int = 50,
    offset: int = 0,
) -> FundamentalOverview:
    resolved_target_date = target_date or date.today()
    publication = read_latest_board_candidate_publication(
        path,
        target_date=target_date,
    )
    lynch_publication = read_latest_published_lynch_daily_batch(
        path,
        resolved_target_date,
    )
    lynch_by_code = (
        lynch_publication.results if lynch_publication is not None else {}
    )
    boards = publication.snapshot.boards if publication else []
    selected_board = (
        next(
            (
                board
                for board in boards
                if board.board_id == board_id
                or board.source_board_code == board_id
            ),
            None,
        )
        if board_id
        else None
    )
    if board_id and selected_board is None:
        raise LookupError("未找到候选板块")

    name_by_code: dict[str, str] = {}
    boards_by_code: dict[str, set[str]] = {}
    for board in boards:
        for member in board.members:
            name_by_code.setdefault(member.code, member.name)
            boards_by_code.setdefault(member.code, set()).add(board.board_id)
    for code, result in lynch_by_code.items():
        name_by_code[code] = result.name

    if selected_board is not None:
        codes = {member.code for member in selected_board.members}
        if lynch_publication is not None:
            codes.intersection_update(lynch_by_code)
    elif lynch_publication is not None:
        codes = set(lynch_by_code)
    else:
        codes = set(name_by_code)
        codes.update(_stored_fundamental_codes(path, target_date=target_date))
    normalized_search = search.strip().casefold()
    if normalized_search:
        codes = {
            code
            for code in codes
            if normalized_search in code.casefold()
            or normalized_search in name_by_code.get(code, "").casefold()
        }

    monthly_by_code = _read_latest_monthly_map(
        path,
        codes,
        target_date=target_date,
    )
    discussion_by_code = _read_latest_discussion_map(
        path,
        codes,
        target_date=target_date,
    )
    items = [
        _overview_item(
            code,
            name=name_by_code.get(code, code),
            board_ids=sorted(boards_by_code.get(code, set())),
            lynch=lynch_by_code.get(code),
            monthly=monthly_by_code.get(code),
            discussion=discussion_by_code.get(code),
        )
        for code in sorted(codes)
    ]
    present = [
        item for item in items if _overview_sort_value(item, sort_by) is not None
    ]
    missing = [
        item for item in items if _overview_sort_value(item, sort_by) is None
    ]
    present.sort(
        key=lambda item: cast(
            str | float | date,
            _overview_sort_value(item, sort_by),
        ),
        reverse=sort_order == "desc",
    )
    items = [*present, *missing]
    return FundamentalOverview(
        board_source=publication.snapshot.source if publication else None,
        board_effective_date=(
            publication.snapshot.effective_date if publication else None
        ),
        board_collected_at=publication.collected_at if publication else None,
        board_complete=publication.snapshot.complete if publication else None,
        boards=boards,
        selected_board_id=selected_board.board_id if selected_board else None,
        lynch_actual_data_date=(
            lynch_publication.target_date if lynch_publication else None
        ),
        lynch_financial_base_date=(
            lynch_publication.financial_base_date
            if lynch_publication
            else None
        ),
        lynch_published_at=(
            lynch_publication.published_at if lynch_publication else None
        ),
        lynch_total_count=len(lynch_by_code),
        lynch_calculable_count=sum(
            item.calculable for item in lynch_by_code.values()
        ),
        lynch_ranking_eligible_count=sum(
            item.ranking_eligible for item in lynch_by_code.values()
        ),
        total=len(items),
        limit=limit,
        offset=offset,
        items=items[offset : offset + limit],
    )


def _stored_fundamental_codes(
    path: Path,
    *,
    target_date: date | None,
) -> set[str]:
    date_filter = ""
    monthly_parameters: tuple[object, ...] = (RULES_VERSION,)
    discussion_parameters: tuple[object, ...] = ()
    if target_date is not None:
        date_filter = "AND as_of_date <= ?"
        monthly_parameters = (
            RULES_VERSION,
            target_date.isoformat(),
        )
        discussion_parameters = (target_date.isoformat(),)
    with sqlite3.connect(path) as connection:
        monthly = connection.execute(
            f"""
            SELECT DISTINCT code
            FROM personal_fundamental_monthly_snapshots
            WHERE rules_version = ?
            {date_filter}
            """,
            monthly_parameters,
        ).fetchall()
        discussion_filter = (
            "WHERE actual_date <= ?" if target_date is not None else ""
        )
        discussion = connection.execute(
            f"""
            SELECT DISTINCT code
            FROM discussion_daily_aggregates
            {discussion_filter}
            """,
            discussion_parameters,
        ).fetchall()
    return {
        cast(str, row[0])
        for row in [*monthly, *discussion]
    }


def _overview_item(
    code: str,
    *,
    name: str,
    board_ids: list[str],
    lynch: LynchDailyResult | None,
    monthly: MonthlyFundamentalRecord | None,
    discussion: DiscussionDayView | None,
) -> FundamentalOverviewItem:
    if monthly is not None and discussion is not None:
        status: FundamentalDataStatus = "complete"
    elif monthly is not None or discussion is not None:
        status = "partial"
    else:
        status = "no_data"
    return FundamentalOverviewItem(
        code=code,
        name=name,
        board_ids=board_ids,
        lynch=lynch,
        monthly=monthly,
        discussion=discussion,
        data_status=status,
    )


def _overview_sort_value(
    item: FundamentalOverviewItem,
    sort_by: OverviewSortField,
) -> str | float | date | None:
    monthly = item.monthly.snapshot if item.monthly else None
    lynch = item.lynch
    values: dict[OverviewSortField, str | float | date | None] = {
        "code": item.code,
        "name": item.name,
        "as_of_date": monthly.as_of_date if monthly else None,
        "floating_market_cap": monthly.floating_market_cap if monthly else None,
        "floating_market_cap_percentile": (
            monthly.floating_market_cap_percentile if monthly else None
        ),
        "adjusted_pe": monthly.adjusted_pe if monthly else None,
        "lynch_growth_value_ratio": (
            monthly.lynch_growth_value_ratio if monthly else None
        ),
        "lynch_ratio": lynch.lynch_ratio if lynch else None,
        "lynch_market_percentile": (
            lynch.market_percentile if lynch else None
        ),
        "true_money_signal_score": (
            monthly.true_money_signal_score if monthly else None
        ),
        "discussion_date": (
            item.discussion.actual_date if item.discussion else None
        ),
    }
    return values[sort_by]


def _read_latest_monthly_map(
    path: Path,
    codes: set[str],
    *,
    target_date: date | None,
) -> dict[str, MonthlyFundamentalRecord]:
    if not codes:
        return {}
    date_filter = ""
    parameters: tuple[object, ...] = (RULES_VERSION,)
    if target_date is not None:
        date_filter = "AND as_of_date <= ?"
        parameters = (RULES_VERSION, target_date.isoformat())
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT code, as_of_date, rules_version, changed_fields_json,
                   source_urls_json, created_at
            FROM personal_fundamental_monthly_snapshots
            WHERE rules_version = ?
            {date_filter}
            ORDER BY code, rules_version, as_of_date, created_at
            """,
            parameters,
        ).fetchall()
    states: dict[tuple[str, str], dict[str, object]] = {}
    latest: dict[str, MonthlyFundamentalRecord] = {}
    for row in rows:
        code = cast(str, row[0])
        if code not in codes:
            continue
        version = cast(str, row[2])
        state = states.setdefault(
            (code, version),
            {"rules_version": version, "code": code},
        )
        state["as_of_date"] = cast(str, row[1])
        state.update(cast(dict[str, object], json.loads(cast(str, row[3]))))
        candidate = MonthlyFundamentalRecord(
            snapshot=PersonalFundamentalMonthlySnapshot.model_validate(
                dict(state)
            ),
            source_urls=cast(list[str], json.loads(cast(str, row[4]))),
            created_at=datetime.fromisoformat(cast(str, row[5])),
        )
        existing = latest.get(code)
        if existing is None or (
            candidate.snapshot.as_of_date,
            candidate.created_at,
        ) > (
            existing.snapshot.as_of_date,
            existing.created_at,
        ):
            latest[code] = candidate
    return latest


def _read_latest_discussion_map(
    path: Path,
    codes: set[str],
    *,
    target_date: date | None,
) -> dict[str, DiscussionDayView]:
    if not codes:
        return {}
    date_filter = ""
    parameters: tuple[object, ...] = ()
    if target_date is not None:
        date_filter = "WHERE actual_date <= ?"
        parameters = (target_date.isoformat(),)
    latest_cte = f"""
        WITH latest AS (
            SELECT code, MAX(actual_date) AS actual_date
            FROM discussion_daily_aggregates
            {date_filter}
            GROUP BY code
        )
    """
    with sqlite3.connect(path) as connection:
        latest_rows = connection.execute(
            f"""
            {latest_cte}
            SELECT code, actual_date
            FROM latest
            """,
            parameters,
        ).fetchall()
        latest_dates = {
            cast(str, row[0]): date.fromisoformat(cast(str, row[1]))
            for row in latest_rows
            if cast(str, row[0]) in codes
        }
        if not latest_dates:
            return {}
        aggregate_rows = connection.execute(
            f"""
            {latest_cte}
            SELECT daily.platform, daily.actual_date, daily.code,
                   daily.post_count, daily.positive_count,
                   daily.neutral_count, daily.negative_count,
                   daily.raw_heat, daily.weighted_sentiment,
                   daily.heat_percentile, daily.likes_missing
            FROM discussion_daily_aggregates AS daily
            INNER JOIN latest
                ON latest.code = daily.code
               AND latest.actual_date = daily.actual_date
            """,
            parameters,
        ).fetchall()
        reference_rows = connection.execute(
            f"""
            {latest_cte}
            SELECT reference.platform, reference.actual_date, reference.code,
                   COUNT(*)
            FROM discussion_post_references AS reference
            INNER JOIN latest
                ON latest.code = reference.code
               AND latest.actual_date = reference.actual_date
            GROUP BY reference.platform, reference.actual_date, reference.code
            """,
            parameters,
        ).fetchall()

    platforms: dict[
        tuple[str, DiscussionPlatform], PlatformDiscussionAggregate
    ] = {}
    for row in aggregate_rows:
        code = cast(str, row[2])
        actual_date = date.fromisoformat(cast(str, row[1]))
        if latest_dates.get(code) != actual_date:
            continue
        platform = cast(DiscussionPlatform, row[0])
        platforms[(code, platform)] = PlatformDiscussionAggregate(
            platform=platform,
            actual_date=actual_date,
            code=code,
            post_count=cast(int, row[3]),
            positive_count=cast(int, row[4]),
            neutral_count=cast(int, row[5]),
            negative_count=cast(int, row[6]),
            raw_heat=cast(float, row[7]),
            weighted_sentiment=cast(float, row[8]),
            heat_percentile=cast(float | None, row[9]),
            likes_missing=bool(row[10]),
        )
    reference_counts: dict[tuple[str, DiscussionPlatform], int] = {}
    for row in reference_rows:
        code = cast(str, row[2])
        actual_date = date.fromisoformat(cast(str, row[1]))
        if latest_dates.get(code) != actual_date:
            continue
        reference_counts[(code, cast(DiscussionPlatform, row[0]))] = cast(
            int, row[3]
        )
    return {
        code: DiscussionDayView(
            actual_date=actual_date,
            code=code,
            eastmoney_guba=platforms.get((code, "eastmoney_guba")),
            xueqiu=platforms.get((code, "xueqiu")),
            eastmoney_reference_count=reference_counts.get(
                (code, "eastmoney_guba"), 0
            ),
            xueqiu_reference_count=reference_counts.get((code, "xueqiu"), 0),
        )
        for code, actual_date in latest_dates.items()
    }


def _require_code(code: str) -> None:
    if len(code) != 6 or not code.isdigit():
        raise ValueError("股票代码必须是六位数字")
