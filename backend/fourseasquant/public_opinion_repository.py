from __future__ import annotations

import sqlite3
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

from fourseasquant.public_opinion import (
    BEIJING,
    ModelOpinionClassification,
    ModelOpinionLabel,
    OpinionContent,
    OpinionPlatform,
    OpinionDirection,
    DirectionStatus,
    PlatformOpinionAggregate,
    classify_public_opinion,
)
from fourseasquant.public_opinion_deepseek import PROMPT_VERSION
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.trading_calendar import is_trading_day


JobStatus = Literal["pending", "running", "succeeded", "failed", "blocked"]
EntryReason = Literal[
    "strategy_target",
    "manual_investigation",
    "explicit_watchlist",
    "eastmoney_hot",
    "tonghuashun_hot",
]
JobTargetSource = Literal["strategy", "manual", "legacy_discovery"]


class OpinionWatchlistEntry(BaseModel):
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class PublicOpinionCollectionJob(BaseModel):
    id: int
    platform: OpinionPlatform
    code: str
    start_date: date
    end_date: date
    trigger: Literal["automatic", "manual"]
    classification_version: str
    status: JobStatus
    cursor: str | None
    created_at: datetime
    updated_at: datetime
    error_summary: str | None
    target_source: JobTargetSource = "legacy_discovery"
    target_version: str = ""
    sample_capped: bool = False
    sample_limit: int | None = None


class StrategyOpinionTargetSnapshot(BaseModel):
    actual_date: date
    strategy_version: str
    codes: list[str]
    published_at: datetime


class PublicOpinionOverviewItem(BaseModel):
    code: str
    name: str
    entry_reason: EntryReason
    entry_reasons: list[EntryReason]
    monitoring_active: bool
    eastmoney: PlatformOpinionAggregate | None = None
    sina: PlatformOpinionAggregate | None = None
    tonghuashun: PlatformOpinionAggregate | None = None


class HotDiscoveryStock(BaseModel):
    code: str
    name: str
    rank: int


class PublicOpinionUniverseItem(BaseModel):
    code: str
    name: str
    entry_reasons: list[EntryReason]
    monitoring_active: bool


class PublicOpinionWindow(BaseModel):
    platform: OpinionPlatform
    code: str
    start_date: date
    end_date: date
    expected_day_count: int
    completed_day_count: int
    content_count: int
    valid_count: int
    favorable_count: int
    unfavorable_count: int
    disputed_count: int
    unknown_count: int
    classified_count: int = 0
    neutral_count: int = 0
    unrelated_count: int = 0
    low_confidence_count: int = 0
    direction_index: float | None
    direction: OpinionDirection | None
    direction_status: DirectionStatus


def create_public_opinion_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_opinion_target_snapshots (
            actual_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            codes_json TEXT NOT NULL CHECK(json_valid(codes_json)),
            target_count INTEGER NOT NULL CHECK(
                target_count BETWEEN 0 AND 10
            ),
            content_sha256 TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_watchlist (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_collection_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            code TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            trigger TEXT NOT NULL,
            classification_version TEXT NOT NULL DEFAULT 'legacy',
            target_source TEXT NOT NULL DEFAULT 'legacy_discovery',
            target_version TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            cursor TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error_summary TEXT,
            CHECK (start_date <= end_date)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_public_opinion_jobs_status
        ON public_opinion_collection_jobs (status, id)
        """
    )
    job_columns = {
        cast(str, row[1])
        for row in connection.execute(
            "PRAGMA table_info(public_opinion_collection_jobs)"
        )
    }
    if "classification_version" not in job_columns:
        connection.execute(
            "ALTER TABLE public_opinion_collection_jobs "
            "ADD COLUMN classification_version TEXT NOT NULL DEFAULT 'legacy'"
        )
    if "target_source" not in job_columns:
        connection.execute(
            "ALTER TABLE public_opinion_collection_jobs "
            "ADD COLUMN target_source TEXT NOT NULL "
            "DEFAULT 'legacy_discovery'"
        )
    if "target_version" not in job_columns:
        connection.execute(
            "ALTER TABLE public_opinion_collection_jobs "
            "ADD COLUMN target_version TEXT NOT NULL DEFAULT ''"
        )
    connection.execute("DROP INDEX IF EXISTS idx_public_opinion_jobs_identity")
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_public_opinion_jobs_identity
        ON public_opinion_collection_jobs (
            platform, code, start_date, end_date, trigger,
            classification_version, target_source, target_version
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_content_references (
            platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            code TEXT NOT NULL,
            actual_date TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            content_kind TEXT NOT NULL,
            source_type TEXT NOT NULL,
            likes INTEGER,
            content_sha256 TEXT NOT NULL,
            sentiment TEXT NOT NULL,
            favorable_terms_json TEXT NOT NULL,
            unfavorable_terms_json TEXT NOT NULL,
            classification_model TEXT,
            classification_confidence REAL,
            prompt_version TEXT,
            rules_version TEXT NOT NULL,
            deleted_at TEXT,
            frozen_at TEXT,
            PRIMARY KEY (platform, content_id, code)
        )
        """
    )
    reference_columns = {
        cast(str, row[1])
        for row in connection.execute(
            "PRAGMA table_info(public_opinion_content_references)"
        )
    }
    for column, definition in (
        ("classification_model", "TEXT"),
        ("classification_confidence", "REAL"),
        ("prompt_version", "TEXT"),
    ):
        if column not in reference_columns:
            connection.execute(
                "ALTER TABLE public_opinion_content_references "
                f"ADD COLUMN {column} {definition}"
            )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_public_opinion_reference_date
        ON public_opinion_content_references (code, actual_date DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_daily_aggregates (
            platform TEXT NOT NULL,
            actual_date TEXT NOT NULL,
            code TEXT NOT NULL,
            collection_complete INTEGER NOT NULL CHECK(collection_complete IN (0, 1)),
            aggregate_json TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (platform, actual_date, code, rules_version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_discovery_snapshots (
            platform TEXT NOT NULL,
            actual_date TEXT NOT NULL,
            complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
            stock_count INTEGER NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY (platform, actual_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_opinion_discovery_entries (
            platform TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            rank INTEGER NOT NULL,
            first_seen_date TEXT NOT NULL,
            last_seen_date TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            dropped_at TEXT,
            retained_until TEXT,
            PRIMARY KEY (platform, code)
        )
        """
    )


def add_to_opinion_watchlist(
    path: Path,
    *,
    code: str,
    name: str,
    now: datetime,
) -> OpinionWatchlistEntry:
    _validate_code(code)
    with open_database_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO public_opinion_watchlist (
                code, name, active, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (code, name, now.isoformat(), now.isoformat()),
        )
    return _read_watchlist_entry(path, code)


def remove_from_opinion_watchlist(
    path: Path,
    *,
    code: str,
    now: datetime,
) -> OpinionWatchlistEntry:
    _validate_code(code)
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            """
            UPDATE public_opinion_watchlist
            SET active = 0, updated_at = ?
            WHERE code = ?
            """,
            (now.isoformat(), code),
        )
        if cursor.rowcount != 1:
            raise LookupError("股票不在重点监测列表")
    return _read_watchlist_entry(path, code)


def list_opinion_watchlist(path: Path) -> list[OpinionWatchlistEntry]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code, name, active, created_at, updated_at
            FROM public_opinion_watchlist
            ORDER BY active DESC, updated_at DESC, code
            """
        ).fetchall()
    return [
        OpinionWatchlistEntry(
            code=str(row[0]),
            name=str(row[1]),
            active=bool(row[2]),
            created_at=datetime.fromisoformat(str(row[3])),
            updated_at=datetime.fromisoformat(str(row[4])),
        )
        for row in rows
    ]


def publish_strategy_opinion_targets(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str,
    codes: list[str],
    published_at: datetime,
) -> StrategyOpinionTargetSnapshot:
    normalized_version = strategy_version.strip()
    if not normalized_version:
        raise ValueError("策略版本不能为空")
    if published_at.tzinfo is None:
        raise ValueError("策略舆论目标发布时间必须包含时区")
    if len(codes) > 10:
        raise ValueError("策略每天最多发布 10 只舆论调查股票")
    if len(set(codes)) != len(codes):
        raise ValueError("策略舆论目标不能包含重复股票代码")
    for code in codes:
        _validate_code(code)
    codes_json = json.dumps(codes, ensure_ascii=False, separators=(",", ":"))
    content_sha256 = hashlib.sha256(codes_json.encode("utf-8")).hexdigest()
    with open_database_connection(path) as connection:
        existing = connection.execute(
            """
            SELECT codes_json, content_sha256, published_at
            FROM strategy_opinion_target_snapshots
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (actual_date.isoformat(), normalized_version),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != content_sha256:
                raise ValueError(
                    "同日同策略版本的目标已发布；修改目标必须使用新策略版本"
                )
            return StrategyOpinionTargetSnapshot(
                actual_date=actual_date,
                strategy_version=normalized_version,
                codes=cast(list[str], json.loads(str(existing[0]))),
                published_at=datetime.fromisoformat(str(existing[2])),
            )
        connection.execute(
            """
            INSERT INTO strategy_opinion_target_snapshots (
                actual_date, strategy_version, codes_json, target_count,
                content_sha256, published_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actual_date.isoformat(),
                normalized_version,
                codes_json,
                len(codes),
                content_sha256,
                published_at.isoformat(),
            ),
        )
    return StrategyOpinionTargetSnapshot(
        actual_date=actual_date,
        strategy_version=normalized_version,
        codes=list(codes),
        published_at=published_at,
    )


def read_strategy_opinion_targets(
    path: Path,
    *,
    actual_date: date,
) -> StrategyOpinionTargetSnapshot | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT actual_date, strategy_version, codes_json, published_at
            FROM strategy_opinion_target_snapshots
            WHERE actual_date = ?
            ORDER BY published_at DESC, strategy_version DESC
            LIMIT 1
            """,
            (actual_date.isoformat(),),
        ).fetchone()
    return None if row is None else _strategy_target_snapshot(row)


def read_latest_strategy_opinion_targets(
    path: Path,
    *,
    as_of_date: date,
) -> StrategyOpinionTargetSnapshot | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT actual_date, strategy_version, codes_json, published_at
            FROM strategy_opinion_target_snapshots
            WHERE actual_date <= ?
            ORDER BY actual_date DESC, published_at DESC,
                     strategy_version DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(),),
        ).fetchone()
    return None if row is None else _strategy_target_snapshot(row)


def create_manual_collection_jobs(
    path: Path,
    *,
    platform: OpinionPlatform,
    code: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> list[PublicOpinionCollectionJob]:
    _validate_code(code)
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    ranges: list[tuple[date, date]] = []
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=29), end_date)
        ranges.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)

    ids: list[int] = []
    with open_database_connection(path) as connection:
        for chunk_start, chunk_end in ranges:
            cursor = connection.execute(
                """
                INSERT INTO public_opinion_collection_jobs (
                    platform, code, start_date, end_date, trigger,
                    classification_version, target_source, target_version,
                    status,
                    cursor, created_at, updated_at, error_summary
                ) VALUES (
                    ?, ?, ?, ?, 'manual', ?, 'manual', '',
                    'pending', NULL, ?, ?, NULL
                )
                """,
                (
                    platform,
                    code,
                    chunk_start.isoformat(),
                    chunk_end.isoformat(),
                    PROMPT_VERSION,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            job_id = cursor.lastrowid
            if job_id is None:
                raise RuntimeError("舆情采集任务写入后没有返回主键")
            ids.append(job_id)
    jobs = list_public_opinion_jobs(path)
    by_id = {job.id: job for job in jobs}
    return [by_id[job_id] for job_id in ids]


def schedule_automatic_collection_jobs(
    path: Path,
    *,
    actual_date: date,
    now: datetime,
) -> list[PublicOpinionCollectionJob]:
    targets = read_strategy_opinion_targets(path, actual_date=actual_date)
    created_ids: list[int] = []
    with open_database_connection(path) as connection:
        connection.execute(
            """
            UPDATE public_opinion_collection_jobs
            SET status = 'blocked', updated_at = ?,
                error_summary = '热榜自动舆论队列已停用，改由策略目标驱动'
            WHERE trigger = 'automatic'
              AND target_source != 'strategy'
              AND status IN ('pending', 'failed', 'running')
            """,
            (now.isoformat(),),
        )
        connection.execute(
            """
            UPDATE public_opinion_collection_jobs
            SET status = 'blocked', updated_at = ?,
                error_summary = '舆论分类版本已升级，改由新版本任务重采'
            WHERE trigger = 'automatic'
              AND classification_version != ?
              AND status IN ('pending', 'failed', 'running')
            """,
            (now.isoformat(), PROMPT_VERSION),
        )
        if targets is None:
            connection.execute(
                """
                UPDATE public_opinion_collection_jobs
                SET status = 'blocked', updated_at = ?,
                    error_summary = '当天没有策略舆论调查目标'
                WHERE trigger = 'automatic'
                  AND status IN ('pending', 'failed', 'running')
                """,
                (now.isoformat(),),
            )
            return []
        placeholders = ",".join("?" for _ in targets.codes)
        stale_clause = (
            f"OR code NOT IN ({placeholders})"
            if targets.codes
            else ""
        )
        connection.execute(
            f"""
            UPDATE public_opinion_collection_jobs
            SET status = 'blocked', updated_at = ?,
                error_summary = '任务不属于当天最新策略舆论目标'
            WHERE trigger = 'automatic'
              AND status IN ('pending', 'failed', 'running')
              AND (
                    target_source != 'strategy'
                    OR target_version != ?
                    OR end_date != ?
                    {stale_clause}
                  )
            """,
            (
                now.isoformat(),
                targets.strategy_version,
                actual_date.isoformat(),
                *targets.codes,
            ),
        )
        for code in targets.codes:
            scheduled_today = connection.execute(
                """
                SELECT 1
                FROM public_opinion_collection_jobs
                WHERE platform = 'sina' AND code = ?
                  AND trigger = 'automatic'
                  AND end_date = ? AND classification_version = ?
                  AND target_source = 'strategy'
                  AND target_version = ?
                LIMIT 1
                """,
                (
                    code,
                    actual_date.isoformat(),
                    PROMPT_VERSION,
                    targets.strategy_version,
                ),
            ).fetchone()
            if scheduled_today is not None:
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO public_opinion_collection_jobs (
                    platform, code, start_date, end_date, trigger,
                    classification_version, target_source, target_version,
                    status, cursor, created_at, updated_at, error_summary
                ) VALUES (
                    'sina', ?, ?, ?, 'automatic', ?, 'strategy', ?,
                    'pending', NULL, ?, ?, NULL
                )
                """,
                (
                    code,
                    (actual_date - timedelta(days=2)).isoformat(),
                    actual_date.isoformat(),
                    PROMPT_VERSION,
                    targets.strategy_version,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                job_id = cursor.lastrowid
                if job_id is None:
                    raise RuntimeError("自动舆论任务写入后没有返回主键")
                created_ids.append(job_id)
    by_id = {job.id: job for job in list_public_opinion_jobs(path)}
    return [by_id[job_id] for job_id in created_ids]


def list_public_opinion_jobs(path: Path) -> list[PublicOpinionCollectionJob]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT id, platform, code, start_date, end_date, trigger, status,
                   classification_version, cursor, created_at, updated_at,
                   error_summary, target_source, target_version
            FROM public_opinion_collection_jobs
            ORDER BY id
            """
        ).fetchall()
    jobs: list[PublicOpinionCollectionJob] = []
    for row in rows:
        raw_cursor = None if row[8] is None else str(row[8])
        sample_capped = False
        sample_limit: int | None = None
        if raw_cursor is not None and raw_cursor.lstrip().startswith("{"):
            try:
                cursor_payload = json.loads(raw_cursor)
                if isinstance(cursor_payload, dict):
                    sample_capped = bool(
                        cursor_payload.get("sample_capped", False)
                    )
                    raw_limit = cursor_payload.get("sample_limit")
                    if isinstance(raw_limit, int):
                        sample_limit = raw_limit
            except json.JSONDecodeError:
                pass
        jobs.append(PublicOpinionCollectionJob(
            id=int(row[0]),
            platform=cast(OpinionPlatform, str(row[1])),
            code=str(row[2]),
            start_date=date.fromisoformat(str(row[3])),
            end_date=date.fromisoformat(str(row[4])),
            trigger=cast(Literal["automatic", "manual"], str(row[5])),
            status=cast(JobStatus, str(row[6])),
            classification_version=str(row[7]),
            cursor=raw_cursor,
            created_at=datetime.fromisoformat(str(row[9])),
            updated_at=datetime.fromisoformat(str(row[10])),
            error_summary=None if row[11] is None else str(row[11]),
            target_source=cast(JobTargetSource, str(row[12])),
            target_version=str(row[13]),
            sample_capped=sample_capped,
            sample_limit=sample_limit,
        ))
    return jobs


def read_public_opinion_job(
    path: Path,
    job_id: int,
) -> PublicOpinionCollectionJob:
    jobs = list_public_opinion_jobs(path)
    try:
        return next(job for job in jobs if job.id == job_id)
    except StopIteration as error:
        raise LookupError("舆论采集任务不存在") from error


def update_public_opinion_job(
    path: Path,
    *,
    job_id: int,
    status: JobStatus,
    updated_at: datetime,
    cursor: str | None = None,
    error_summary: str | None = None,
) -> PublicOpinionCollectionJob:
    with open_database_connection(path) as connection:
        result = connection.execute(
            """
            UPDATE public_opinion_collection_jobs
            SET status = ?, cursor = ?, updated_at = ?, error_summary = ?
            WHERE id = ?
            """,
            (
                status,
                cursor,
                updated_at.isoformat(),
                error_summary,
                job_id,
            ),
        )
        if result.rowcount != 1:
            raise LookupError("舆论采集任务不存在")
    return read_public_opinion_job(path, job_id)


def read_public_opinion_overview(
    path: Path,
    *,
    as_of_date: date | None = None,
) -> list[PublicOpinionOverviewItem]:
    latest = _read_latest_aggregate_map(path)
    selected_date = as_of_date or datetime.now(BEIJING).date()
    targets = read_latest_strategy_opinion_targets(
        path,
        as_of_date=selected_date,
    )
    watchlist = list_opinion_watchlist(path)
    manual_codes = {
        job.code
        for job in list_public_opinion_jobs(path)
        if job.trigger == "manual"
    }
    target_codes = [] if targets is None else targets.codes
    names = _read_security_names(
        path,
        set(target_codes) | manual_codes | {entry.code for entry in watchlist},
    )
    universe: dict[str, PublicOpinionUniverseItem] = {}
    for code in target_codes:
        universe[code] = PublicOpinionUniverseItem(
            code=code,
            name=names.get(code, code),
            entry_reasons=["strategy_target"],
            monitoring_active=True,
        )
    for code in sorted(manual_codes):
        existing = universe.get(code)
        if existing is None:
            universe[code] = PublicOpinionUniverseItem(
                code=code,
                name=names.get(code, code),
                entry_reasons=["manual_investigation"],
                monitoring_active=True,
            )
        elif "manual_investigation" not in existing.entry_reasons:
            existing.entry_reasons.append("manual_investigation")
    for entry in watchlist:
        existing = universe.get(entry.code)
        if existing is None:
            universe[entry.code] = PublicOpinionUniverseItem(
                code=entry.code,
                name=entry.name,
                entry_reasons=["explicit_watchlist"],
                monitoring_active=entry.active,
            )
        elif "explicit_watchlist" not in existing.entry_reasons:
            existing.entry_reasons.append("explicit_watchlist")
    return [
        PublicOpinionOverviewItem(
            code=item.code,
            name=item.name,
            entry_reason=item.entry_reasons[0],
            entry_reasons=item.entry_reasons,
            monitoring_active=item.monitoring_active,
            eastmoney=latest.get((item.code, "eastmoney")),
            sina=latest.get((item.code, "sina")),
            tonghuashun=latest.get((item.code, "tonghuashun")),
        )
        for item in universe.values()
    ]


def sync_hot_discovery_snapshot(
    path: Path,
    *,
    platform: Literal["eastmoney", "tonghuashun"],
    actual_date: date,
    stocks: list[HotDiscoveryStock],
    collected_at: datetime,
    complete: bool,
) -> None:
    if not complete:
        raise ValueError("不完整热榜不能替换当前发现池")
    if len({stock.code for stock in stocks}) != len(stocks):
        raise ValueError("完整热榜中存在重复股票代码")
    for stock in stocks:
        _validate_code(stock.code)
        if stock.rank < 1:
            raise ValueError("热榜排名必须大于零")
    present_codes = {stock.code for stock in stocks}
    retained_until = _add_trading_days(actual_date, 4)
    with open_database_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO public_opinion_discovery_snapshots (
                platform, actual_date, complete, stock_count, collected_at
            ) VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(platform, actual_date) DO UPDATE SET
                complete = 1,
                stock_count = excluded.stock_count,
                collected_at = excluded.collected_at
            """,
            (
                platform,
                actual_date.isoformat(),
                len(stocks),
                collected_at.isoformat(),
            ),
        )
        for stock in stocks:
            connection.execute(
                """
                INSERT INTO public_opinion_discovery_entries (
                    platform, code, name, rank, first_seen_date,
                    last_seen_date, active, dropped_at, retained_until
                ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, NULL)
                ON CONFLICT(platform, code) DO UPDATE SET
                    name = excluded.name,
                    rank = excluded.rank,
                    last_seen_date = excluded.last_seen_date,
                    active = 1,
                    dropped_at = NULL,
                    retained_until = NULL
                """,
                (
                    platform,
                    stock.code,
                    stock.name,
                    stock.rank,
                    actual_date.isoformat(),
                    actual_date.isoformat(),
                ),
            )
        active_rows = connection.execute(
            """
            SELECT code
            FROM public_opinion_discovery_entries
            WHERE platform = ? AND active = 1
            """,
            (platform,),
        ).fetchall()
        for row in active_rows:
            code = str(row[0])
            if code in present_codes:
                continue
            connection.execute(
                """
                UPDATE public_opinion_discovery_entries
                SET active = 0, dropped_at = ?, retained_until = ?
                WHERE platform = ? AND code = ?
                """,
                (
                    actual_date.isoformat(),
                    retained_until.isoformat(),
                    platform,
                    code,
                ),
            )


def read_public_opinion_universe(
    path: Path,
    *,
    as_of_date: date,
) -> list[PublicOpinionUniverseItem]:
    with open_database_connection(path) as connection:
        discovery_rows = connection.execute(
            """
            SELECT platform, code, name, rank, active
            FROM public_opinion_discovery_entries
            WHERE active = 1 OR retained_until >= ?
            ORDER BY active DESC, rank, code
            """,
            (as_of_date.isoformat(),),
        ).fetchall()
        watchlist_rows = connection.execute(
            """
            SELECT code, name, active
            FROM public_opinion_watchlist
            WHERE active = 1
            ORDER BY updated_at DESC, code
            """
        ).fetchall()
    items: dict[str, PublicOpinionUniverseItem] = {}
    for row in discovery_rows:
        platform = cast(Literal["eastmoney", "tonghuashun"], str(row[0]))
        reason: EntryReason = (
            "eastmoney_hot" if platform == "eastmoney" else "tonghuashun_hot"
        )
        code = str(row[1])
        existing = items.get(code)
        if existing is None:
            items[code] = PublicOpinionUniverseItem(
                code=code,
                name=str(row[2]),
                entry_reasons=[reason],
                monitoring_active=True,
            )
        elif reason not in existing.entry_reasons:
            existing.entry_reasons.append(reason)
    for row in watchlist_rows:
        code = str(row[0])
        existing = items.get(code)
        if existing is None:
            items[code] = PublicOpinionUniverseItem(
                code=code,
                name=str(row[1]),
                entry_reasons=["explicit_watchlist"],
                monitoring_active=bool(row[2]),
            )
        elif "explicit_watchlist" not in existing.entry_reasons:
            existing.entry_reasons.insert(0, "explicit_watchlist")
    return list(items.values())


def read_public_opinion_window(
    path: Path,
    *,
    platform: OpinionPlatform,
    code: str,
    end_date: date,
    days: int,
    rules_version: str = PROMPT_VERSION,
) -> PublicOpinionWindow:
    _validate_code(code)
    if days < 1:
        raise ValueError("舆论窗口天数必须大于零")
    start_date = end_date - timedelta(days=days - 1)
    with open_database_connection(path) as connection:
        completed_day_count = int(
            connection.execute(
                """
                WITH ranked AS (
                    SELECT actual_date, collection_complete,
                           ROW_NUMBER() OVER (
                               PARTITION BY actual_date
                               ORDER BY published_at DESC, rules_version DESC
                           ) AS position
                    FROM public_opinion_daily_aggregates
                    WHERE platform = ? AND code = ?
                      AND actual_date BETWEEN ? AND ?
                      AND rules_version = ?
                )
                SELECT COUNT(*)
                FROM ranked
                WHERE position = 1 AND collection_complete = 1
                """,
                (
                    platform,
                    code,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    rules_version,
                ),
            ).fetchone()[0]
        )
        prompt_filter = (
            "AND prompt_version = ?"
            if rules_version.startswith("public-opinion-deepseek-")
            else "AND prompt_version IS NULL"
        )
        prompt_parameters: tuple[str, ...] = (
            (rules_version,)
            if rules_version.startswith("public-opinion-deepseek-")
            else ()
        )
        rows = connection.execute(
            f"""
            SELECT sentiment, likes, classification_model,
                   classification_confidence
            FROM public_opinion_content_references
            WHERE platform = ? AND code = ?
              AND actual_date BETWEEN ? AND ?
              AND (
                    deleted_at IS NULL
                    OR (
                        frozen_at IS NOT NULL
                        AND deleted_at > frozen_at
                    )
                  )
              {prompt_filter}
            """,
            (
                platform,
                code,
                start_date.isoformat(),
                end_date.isoformat(),
                *prompt_parameters,
            ),
        ).fetchall()
    counts = {
        "favorable": 0,
        "unfavorable": 0,
        "disputed": 0,
        "neutral": 0,
        "unrelated": 0,
        "unknown": 0,
    }
    weights = {
        "favorable": 0.0,
        "unfavorable": 0.0,
        "disputed": 0.0,
        "neutral": 0.0,
    }
    classified_count = 0
    low_confidence_count = 0
    for row in rows:
        sentiment = str(row[0])
        if sentiment not in counts:
            continue
        counts[sentiment] += 1
        if row[2] is not None:
            classified_count += 1
        if row[3] is not None and float(row[3]) < 0.7:
            low_confidence_count += 1
        if sentiment in {"unknown", "unrelated"}:
            continue
        likes = None if row[1] is None else int(row[1])
        weight = 1.0 if likes is None else 1 + math.log(1 + likes)
        weights[sentiment] += weight
    valid_count = (
        counts["favorable"] + counts["unfavorable"] + counts["disputed"]
        + counts["neutral"]
    )
    denominator = sum(weights.values())
    direction_index = (
        (weights["favorable"] - weights["unfavorable"]) / denominator
        if denominator > 0
        else None
    )
    if completed_day_count < days:
        direction_status: DirectionStatus = "collecting"
        direction: OpinionDirection | None = None
    elif valid_count < 10:
        direction_status = "insufficient_sample"
        direction = None
    else:
        direction_status = "published"
        assert direction_index is not None
        direction = (
            "favorable"
            if direction_index >= 0.2
            else "unfavorable"
            if direction_index <= -0.2
            else "balanced"
        )
    return PublicOpinionWindow(
        platform=platform,
        code=code,
        start_date=start_date,
        end_date=end_date,
        expected_day_count=days,
        completed_day_count=completed_day_count,
        content_count=len(rows),
        valid_count=valid_count,
        favorable_count=counts["favorable"],
        unfavorable_count=counts["unfavorable"],
        disputed_count=counts["disputed"],
        unknown_count=counts["unknown"],
        classified_count=classified_count,
        neutral_count=counts["neutral"],
        unrelated_count=counts["unrelated"],
        low_confidence_count=low_confidence_count,
        direction_index=direction_index,
        direction=direction,
        direction_status=direction_status,
    )


def mark_public_opinion_deleted(
    path: Path,
    *,
    platform: OpinionPlatform,
    content_id: str,
    code: str,
    detected_at: datetime,
) -> None:
    _validate_code(code)
    if detected_at.tzinfo is None:
        raise ValueError("删除检测时间必须包含时区")
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            """
            UPDATE public_opinion_content_references
            SET deleted_at = ?
            WHERE platform = ? AND content_id = ? AND code = ?
            """,
            (
                detected_at.isoformat(),
                platform,
                content_id,
                code,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError("未找到要标记删除的舆论内容引用")


def save_public_opinion_day(
    path: Path,
    *,
    contents: list[OpinionContent],
    classifications: Mapping[str, ModelOpinionClassification] | None = None,
    aggregate: PlatformOpinionAggregate,
    collected_at: datetime,
) -> None:
    if any(
        content.platform != aggregate.platform
        or content.code != aggregate.code
        or content.published_at.astimezone(BEIJING).date()
        != aggregate.actual_date
        for content in contents
    ):
        raise ValueError("内容引用与平台日汇总不一致")
    if classifications is not None and {
        content.content_id for content in contents
    } != set(classifications):
        raise ValueError("大模型分类结果与保存内容编号不一致")
    with open_database_connection(path) as connection:
        for content in contents:
            model_classification = (
                None
                if classifications is None
                else classifications[content.content_id]
            )
            classified = (
                classify_public_opinion(content.text, content.likes)
                if model_classification is None
                else None
            )
            sentiment: str
            if model_classification is None:
                assert classified is not None
                sentiment = classified.sentiment
                favorable_terms = classified.favorable_terms
                unfavorable_terms = classified.unfavorable_terms
                classification_model = None
                classification_confidence = None
                prompt_version = None
            else:
                sentiment = model_classification.label
                favorable_terms = []
                unfavorable_terms = []
                classification_model = model_classification.model
                classification_confidence = model_classification.confidence
                prompt_version = model_classification.prompt_version
            digest = hashlib.sha256(content.text.encode("utf-8")).hexdigest()
            freeze_at = datetime.combine(
                content.published_at.astimezone(BEIJING).date()
                + timedelta(days=3),
                datetime.min.time(),
                tzinfo=BEIJING,
            )
            existing = connection.execute(
                """
                SELECT frozen_at, prompt_version
                FROM public_opinion_content_references
                WHERE platform = ? AND content_id = ? AND code = ?
                """,
                (content.platform, content.content_id, content.code),
            ).fetchone()
            if existing is not None and existing[0] is not None:
                if (
                    model_classification is not None
                    and existing[1] != model_classification.prompt_version
                ):
                    connection.execute(
                        """
                        UPDATE public_opinion_content_references
                        SET url = ?, collected_at = ?, content_sha256 = ?,
                            sentiment = ?, favorable_terms_json = '[]',
                            unfavorable_terms_json = '[]',
                            classification_model = ?,
                            classification_confidence = ?,
                            prompt_version = ?, rules_version = ?
                        WHERE platform = ? AND content_id = ? AND code = ?
                        """,
                        (
                            content.url,
                            content.collected_at.isoformat(),
                            digest,
                            sentiment,
                            classification_model,
                            classification_confidence,
                            prompt_version,
                            aggregate.rules_version,
                            content.platform,
                            content.content_id,
                            content.code,
                        ),
                    )
                continue
            if existing is not None and collected_at >= freeze_at:
                connection.execute(
                    """
                    UPDATE public_opinion_content_references
                    SET frozen_at = ?
                    WHERE platform = ? AND content_id = ? AND code = ?
                    """,
                    (
                        freeze_at.isoformat(),
                        content.platform,
                        content.content_id,
                        content.code,
                    ),
                )
                continue
            values = (
                content.platform,
                content.content_id,
                content.code,
                aggregate.actual_date.isoformat(),
                content.url,
                content.published_at.isoformat(),
                content.collected_at.isoformat(),
                content.content_kind,
                content.source_type,
                content.likes,
                digest,
                sentiment,
                json.dumps(
                    favorable_terms,
                    ensure_ascii=False,
                ),
                json.dumps(
                    unfavorable_terms,
                    ensure_ascii=False,
                ),
                classification_model,
                classification_confidence,
                prompt_version,
                aggregate.rules_version,
                freeze_at.isoformat() if collected_at >= freeze_at else None,
            )
            connection.execute(
                """
                INSERT INTO public_opinion_content_references (
                    platform, content_id, code, actual_date, url,
                    published_at, collected_at, content_kind, source_type,
                    likes, content_sha256, sentiment, favorable_terms_json,
                    unfavorable_terms_json, classification_model,
                    classification_confidence, prompt_version, rules_version,
                    deleted_at, frozen_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    NULL, ?
                )
                ON CONFLICT(platform, content_id, code) DO UPDATE SET
                    url = excluded.url,
                    collected_at = excluded.collected_at,
                    likes = excluded.likes,
                    content_sha256 = excluded.content_sha256,
                    sentiment = excluded.sentiment,
                    favorable_terms_json = excluded.favorable_terms_json,
                    unfavorable_terms_json = excluded.unfavorable_terms_json,
                    classification_model = excluded.classification_model,
                    classification_confidence =
                        excluded.classification_confidence,
                    prompt_version = excluded.prompt_version,
                    rules_version = excluded.rules_version
                WHERE public_opinion_content_references.frozen_at IS NULL
                """,
                values,
            )
        aggregate_freeze_date = aggregate.actual_date + timedelta(days=3)
        if collected_at.astimezone(BEIJING).date() < aggregate_freeze_date:
            connection.execute(
                """
                INSERT INTO public_opinion_daily_aggregates (
                    platform, actual_date, code, collection_complete,
                    aggregate_json, rules_version, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, actual_date, code, rules_version)
                DO UPDATE SET
                    collection_complete = excluded.collection_complete,
                    aggregate_json = excluded.aggregate_json,
                    published_at = excluded.published_at
                """,
                (
                    aggregate.platform,
                    aggregate.actual_date.isoformat(),
                    aggregate.code,
                    int(aggregate.collection_complete),
                    aggregate.model_dump_json(),
                    aggregate.rules_version,
                    collected_at.isoformat(),
                ),
            )
        else:
            connection.execute(
                """
                INSERT OR IGNORE INTO public_opinion_daily_aggregates (
                    platform, actual_date, code, collection_complete,
                    aggregate_json, rules_version, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate.platform,
                    aggregate.actual_date.isoformat(),
                    aggregate.code,
                    int(aggregate.collection_complete),
                    aggregate.model_dump_json(),
                    aggregate.rules_version,
                    collected_at.isoformat(),
                ),
            )


def read_cached_model_classifications(
    path: Path,
    *,
    contents: list[OpinionContent],
    model: str,
    prompt_version: str,
) -> dict[str, ModelOpinionClassification]:
    cached: dict[str, ModelOpinionClassification] = {}
    with open_database_connection(path) as connection:
        for content in contents:
            digest = hashlib.sha256(content.text.encode("utf-8")).hexdigest()
            row = connection.execute(
                """
                SELECT sentiment, classification_confidence
                FROM public_opinion_content_references
                WHERE platform = ? AND content_id = ? AND code = ?
                  AND content_sha256 = ?
                  AND classification_model = ?
                  AND prompt_version = ?
                  AND classification_confidence IS NOT NULL
                """,
                (
                    content.platform,
                    content.content_id,
                    content.code,
                    digest,
                    model,
                    prompt_version,
                ),
            ).fetchone()
            if row is None:
                continue
            label = str(row[0])
            if label not in {
                "favorable",
                "unfavorable",
                "disputed",
                "neutral",
                "unrelated",
            }:
                continue
            cached[content.content_id] = ModelOpinionClassification(
                content_id=content.content_id,
                label=cast(ModelOpinionLabel, label),
                confidence=float(row[1]),
                model=model,
                prompt_version=prompt_version,
            )
    return cached


def count_public_opinion_model_contents(
    path: Path,
    *,
    platform: OpinionPlatform,
    code: str,
    start_date: date,
    end_date: date,
    prompt_version: str,
) -> int:
    return len(read_public_opinion_model_content_ids(
        path,
        platform=platform,
        code=code,
        start_date=start_date,
        end_date=end_date,
        prompt_version=prompt_version,
    ))


def read_public_opinion_model_content_ids(
    path: Path,
    *,
    platform: OpinionPlatform,
    code: str,
    start_date: date,
    end_date: date,
    prompt_version: str,
) -> set[str]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT content_id
            FROM public_opinion_content_references
            WHERE platform = ? AND code = ?
              AND actual_date BETWEEN ? AND ?
              AND prompt_version = ?
            """,
            (
                platform,
                code,
                start_date.isoformat(),
                end_date.isoformat(),
                prompt_version,
            ),
        ).fetchall()
    return {str(row[0]) for row in rows}


def rebuild_public_opinion_day(
    path: Path,
    *,
    platform: OpinionPlatform,
    code: str,
    actual_date: date,
    collection_complete: bool,
    collected_at: datetime,
    rules_version: str,
    sample_capped: bool = False,
    sample_limit: int | None = None,
) -> PlatformOpinionAggregate:
    model_version = rules_version.startswith("public-opinion-deepseek-")
    version_clause = (
        "AND prompt_version = ?"
        if model_version
        else "AND prompt_version IS NULL"
    )
    parameters: tuple[object, ...] = (
        platform,
        code,
        actual_date.isoformat(),
        *((rules_version,) if model_version else ()),
    )
    with open_database_connection(path) as connection:
        rows = connection.execute(
            f"""
            SELECT sentiment, likes, classification_model,
                   classification_confidence
            FROM public_opinion_content_references
            WHERE platform = ? AND code = ? AND actual_date = ?
              {version_clause}
              AND (
                    deleted_at IS NULL
                    OR (
                        frozen_at IS NOT NULL
                        AND deleted_at > frozen_at
                    )
                  )
            """,
            parameters,
        ).fetchall()
    counts = {
        "favorable": 0,
        "unfavorable": 0,
        "disputed": 0,
        "neutral": 0,
        "unrelated": 0,
        "unknown": 0,
    }
    weights = {
        "favorable": 0.0,
        "unfavorable": 0.0,
        "disputed": 0.0,
        "neutral": 0.0,
    }
    likes_missing = False
    classified_count = 0
    low_confidence_count = 0
    for row in rows:
        sentiment = str(row[0])
        if sentiment not in counts:
            continue
        counts[sentiment] += 1
        likes = None if row[1] is None else int(row[1])
        likes_missing = likes_missing or likes is None
        if row[2] is not None:
            classified_count += 1
        if row[3] is not None and float(row[3]) < 0.7:
            low_confidence_count += 1
        if sentiment in {"unknown", "unrelated"}:
            continue
        weights[sentiment] += (
            1.0 if likes is None else 1 + math.log(1 + likes)
        )
    valid_count = (
        counts["favorable"] + counts["unfavorable"] + counts["disputed"]
        + counts["neutral"]
    )
    denominator = sum(weights.values())
    direction_index = (
        (weights["favorable"] - weights["unfavorable"]) / denominator
        if denominator > 0
        else None
    )
    if not collection_complete:
        direction_status: DirectionStatus = "collecting"
        direction: OpinionDirection | None = None
    elif valid_count < 10:
        direction_status = "insufficient_sample"
        direction = None
    else:
        direction_status = "published"
        assert direction_index is not None
        direction = (
            "favorable"
            if direction_index >= 0.2
            else "unfavorable"
            if direction_index <= -0.2
            else "balanced"
        )
    aggregate = PlatformOpinionAggregate(
        platform=platform,
        actual_date=actual_date,
        code=code,
        collection_complete=collection_complete,
        content_count=len(rows),
        valid_count=valid_count,
        favorable_count=counts["favorable"],
        unfavorable_count=counts["unfavorable"],
        disputed_count=counts["disputed"],
        unknown_count=counts["unknown"],
        classified_count=classified_count,
        neutral_count=counts["neutral"],
        unrelated_count=counts["unrelated"],
        low_confidence_count=low_confidence_count,
        weighted_favorable=weights["favorable"],
        weighted_unfavorable=weights["unfavorable"],
        weighted_disputed=weights["disputed"],
        weighted_neutral=weights["neutral"],
        direction_index=direction_index,
        direction=direction,
        direction_status=direction_status,
        likes_missing=likes_missing,
        rules_version=rules_version,
        sample_capped=sample_capped,
        sample_limit=sample_limit,
    )
    save_public_opinion_day(
        path,
        contents=[],
        aggregate=aggregate,
        collected_at=collected_at,
    )
    return aggregate


def _read_latest_aggregate_map(
    path: Path,
) -> dict[tuple[str, OpinionPlatform], PlatformOpinionAggregate]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            WITH latest AS (
                SELECT code, platform, MAX(actual_date) AS actual_date
                FROM public_opinion_daily_aggregates
                GROUP BY code, platform
            )
            SELECT daily.code, daily.platform, daily.aggregate_json
            FROM public_opinion_daily_aggregates AS daily
            INNER JOIN latest
                ON latest.code = daily.code
               AND latest.platform = daily.platform
               AND latest.actual_date = daily.actual_date
            ORDER BY daily.code, daily.platform, daily.published_at DESC,
                     daily.rules_version DESC
            """
        ).fetchall()
    result: dict[tuple[str, OpinionPlatform], PlatformOpinionAggregate] = {}
    for row in rows:
        platform = cast(OpinionPlatform, str(row[1]))
        result.setdefault(
            (str(row[0]), platform),
            PlatformOpinionAggregate.model_validate_json(str(row[2])),
        )
    return result


def _read_watchlist_entry(path: Path, code: str) -> OpinionWatchlistEntry:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT code, name, active, created_at, updated_at
            FROM public_opinion_watchlist
            WHERE code = ?
            """,
            (code,),
        ).fetchone()
    if row is None:
        raise LookupError("股票不在重点监测列表")
    return OpinionWatchlistEntry(
        code=str(row[0]),
        name=str(row[1]),
        active=bool(row[2]),
        created_at=datetime.fromisoformat(str(row[3])),
        updated_at=datetime.fromisoformat(str(row[4])),
    )


def _strategy_target_snapshot(
    row: tuple[object, ...],
) -> StrategyOpinionTargetSnapshot:
    raw_codes = json.loads(str(row[2]))
    if not isinstance(raw_codes, list) or not all(
        isinstance(code, str) for code in raw_codes
    ):
        raise ValueError("策略舆论目标快照内容无效")
    return StrategyOpinionTargetSnapshot(
        actual_date=date.fromisoformat(str(row[0])),
        strategy_version=str(row[1]),
        codes=cast(list[str], raw_codes),
        published_at=datetime.fromisoformat(str(row[3])),
    )


def _read_security_names(path: Path, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    ordered_codes = sorted(codes)
    with open_database_connection(path) as connection:
        rows = connection.execute(
            f"""
            WITH latest AS (
                SELECT code, MAX(actual_data_date) AS actual_data_date
                FROM historical_security_facts
                WHERE code IN ({placeholders})
                GROUP BY code
            )
            SELECT fact.code, fact.name
            FROM historical_security_facts AS fact
            INNER JOIN latest
                ON latest.code = fact.code
               AND latest.actual_data_date = fact.actual_data_date
            ORDER BY fact.code
            """,
            ordered_codes,
        ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _validate_code(code: str) -> None:
    if len(code) != 6 or not code.isdigit():
        raise ValueError("股票代码必须是6位数字")


def _add_trading_days(start_date: date, additional_days: int) -> date:
    result = start_date
    remaining = additional_days
    while remaining > 0:
        result += timedelta(days=1)
        if is_trading_day(result):
            remaining -= 1
    return result
