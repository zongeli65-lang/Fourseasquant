from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fourseasquant.sqlite_connection import open_database_connection

from .fresh_queue import NEWS_FRESHNESS, read_fresh_news_funnel
from .policy import ACTIVE_SELECTION_RULES_VERSION


BEIJING = ZoneInfo("Asia/Shanghai")
CATCHUP_LIMIT = timedelta(hours=72)
TRADING_DAY_SCANS = (time(8, 15), time(11, 35), time(15, 20), time(21, 30))
NON_TRADING_DAY_SCANS = (time(10, 0), time(21, 0))
TriggerType = Literal["keyword", "url", "message"]


class HuntingDisabledError(RuntimeError):
    """用户已经关闭产业链狩猎服务。"""


@dataclass(frozen=True)
class HuntingRequestRecord:
    request_id: str
    trigger_method: str
    trigger_type: str
    trigger_content: str
    source_url: str | None
    as_of_time: datetime
    priority: int
    status: str
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_summary: str | None


@dataclass(frozen=True)
class RuntimeStatus:
    enabled: bool
    state: str
    paused_at: datetime | None
    resumed_at: datetime | None
    catchup_from: datetime | None
    worker_heartbeat_at: datetime | None
    worker_online: bool
    last_poll_at: datetime | None
    last_model_run_at: datetime | None
    last_cleanup_at: datetime | None
    error_summary: str | None
    queued_count: int
    running_count: int
    discovered_count: int
    triaged_discovery_count: int
    untriaged_count: int
    research_material_count: int
    event_cluster_count: int
    investigated_company_count: int
    freshness_cutoff: datetime
    deep_hunt_count: int
    completed_selection_count: int
    invalid_event_count: int
    failed_hunt_count: int
    published_count: int
    next_scheduled_scan_at: datetime | None
    updated_at: datetime


def initialize_runtime_control(
    connection: sqlite3.Connection,
    *,
    now: datetime,
) -> None:
    timestamp = now.astimezone(BEIJING).isoformat()
    connection.execute(
        """
        INSERT OR IGNORE INTO industry_chain_runtime_control (
            id, enabled, state, paused_at, resumed_at, catchup_from,
            worker_heartbeat_at, last_poll_at, last_model_run_at,
            last_cleanup_at, error_summary, updated_at
        ) VALUES (1, 0, 'paused', ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?)
        """,
        (timestamp, timestamp),
    )


def read_runtime_status(
    path: Path,
    *,
    now: datetime | None = None,
) -> RuntimeStatus:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT enabled, state, paused_at, resumed_at, catchup_from,
                   worker_heartbeat_at, last_poll_at, last_model_run_at,
                   last_cleanup_at, error_summary, updated_at
            FROM industry_chain_runtime_control
            WHERE id = 1
            """
        ).fetchone()
        counts = dict(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """
                    SELECT status, COUNT(*)
                    FROM industry_chain_hunting_requests
                    WHERE status IN ('queued', 'running', 'paused')
                    GROUP BY status
                    """
                ).fetchall(),
            )
        )
        activity = connection.execute(
            """
            SELECT
                (SELECT COUNT(*)
                 FROM industry_chain_hunting_requests
                 WHERE trigger_type = 'source_event'),
                (SELECT COUNT(*)
                 FROM industry_chain_selections AS selection
                 WHERE selection.status = 'selected'
                   AND json_extract(
                       selection.snapshot_json, '$.rules_version'
                   ) = ?
                   AND NOT EXISTS (
                     SELECT 1
                     FROM industry_chain_selection_dismissals AS dismissal
                     WHERE dismissal.selection_id = selection.selection_id
                       AND dismissal.selection_version =
                           selection.selection_version
                 )),
                (SELECT COUNT(*)
                 FROM industry_chain_event_clusters AS cluster
                 WHERE cluster.last_seen_at >= ?
                   AND cluster.first_seen_at <= ?),
                (SELECT COUNT(DISTINCT json_extract(
                            company.value, '$.code'
                        ))
                 FROM industry_chain_search_runs AS search,
                      json_each(
                          CASE
                              WHEN json_valid(search.plan_json)
                              THEN search.plan_json
                              ELSE '{}'
                          END,
                          '$.recalled_companies'
                      ) AS company
                 WHERE search.completed_at >= ?
                   AND search.completed_at <= ?),
                (SELECT COUNT(*)
                 FROM industry_chain_selection_runs AS run
                 WHERE run.rules_version = ?
                   AND run.completed_at >= ?
                   AND run.completed_at <= ?),
                (SELECT COUNT(*)
                 FROM industry_chain_hunting_requests AS request
                 WHERE request.trigger_type = 'source_event'
                   AND request.status = 'succeeded'
                   AND request.completed_at >= ?
                   AND request.completed_at <= ?
                   AND NOT EXISTS (
                     SELECT 1
                     FROM industry_chain_selections AS selection
                     WHERE selection.selection_id =
                           'ics-' || request.request_id
                   )),
                (SELECT COUNT(*)
                 FROM industry_chain_hunting_requests AS request
                 WHERE request.trigger_type = 'source_event'
                   AND request.status = 'failed'
                   AND request.completed_at >= ?
                   AND request.completed_at <= ?)
            """,
            (
                ACTIVE_SELECTION_RULES_VERSION,
                (current - NEWS_FRESHNESS).isoformat(),
                current.isoformat(),
                (current - NEWS_FRESHNESS).isoformat(),
                current.isoformat(),
                ACTIVE_SELECTION_RULES_VERSION,
                (current - NEWS_FRESHNESS).isoformat(),
                current.isoformat(),
                (current - NEWS_FRESHNESS).isoformat(),
                current.isoformat(),
                (current - NEWS_FRESHNESS).isoformat(),
                current.isoformat(),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("产业链运行控制尚未初始化")
    heartbeat = _optional_datetime(row[5])
    enabled = bool(row[0])
    if activity is None:
        raise RuntimeError("产业链处理进度读取失败")
    funnel = read_fresh_news_funnel(path, as_of_time=current)
    return RuntimeStatus(
        enabled=enabled,
        state=cast(str, row[1]),
        paused_at=_optional_datetime(row[2]),
        resumed_at=_optional_datetime(row[3]),
        catchup_from=_optional_datetime(row[4]),
        worker_heartbeat_at=heartbeat,
        worker_online=bool(
            heartbeat is not None
            and current - heartbeat <= timedelta(minutes=2)
        ),
        last_poll_at=_optional_datetime(row[6]),
        last_model_run_at=_optional_datetime(row[7]),
        last_cleanup_at=_optional_datetime(row[8]),
        error_summary=cast(str | None, row[9]),
        queued_count=counts.get("queued", 0) + counts.get("paused", 0),
        running_count=counts.get("running", 0),
        discovered_count=funnel.collected_count,
        triaged_discovery_count=funnel.triaged_count,
        untriaged_count=funnel.pending_triage_count,
        research_material_count=funnel.research_material_count,
        event_cluster_count=cast(int, activity[2]),
        investigated_company_count=cast(int, activity[3]),
        freshness_cutoff=funnel.freshness_cutoff,
        deep_hunt_count=cast(int, activity[0]),
        completed_selection_count=cast(int, activity[4]),
        invalid_event_count=cast(int, activity[5]),
        failed_hunt_count=cast(int, activity[6]),
        published_count=cast(int, activity[1]),
        next_scheduled_scan_at=(
            next_scheduled_scan(current) if enabled else None
        ),
        updated_at=datetime.fromisoformat(cast(str, row[10])),
    )


def set_hunting_enabled(
    path: Path,
    *,
    enabled: bool,
    now: datetime | None = None,
) -> RuntimeStatus:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    timestamp = current.isoformat()
    with open_database_connection(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            """
            SELECT enabled, paused_at
            FROM industry_chain_runtime_control
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("产业链运行控制尚未初始化")
        was_enabled = bool(row[0])
        if was_enabled == enabled:
            return read_runtime_status(path, now=current)
        with connection:
            if enabled:
                paused_at = _optional_datetime(row[1]) or current
                catchup_from = max(paused_at, current - CATCHUP_LIMIT)
                connection.execute(
                    """
                    UPDATE industry_chain_runtime_control
                    SET enabled = 1, state = 'running', resumed_at = ?,
                        catchup_from = ?, error_summary = NULL, updated_at = ?
                    WHERE id = 1
                    """,
                    (timestamp, catchup_from.isoformat(), timestamp),
                )
                connection.execute(
                    """
                    UPDATE industry_chain_hunting_requests
                    SET status = 'queued'
                    WHERE status = 'paused'
                    """
                )
                _insert_request(
                    connection,
                    trigger_method="resume_catchup",
                    trigger_type="catchup_window",
                    trigger_content=json.dumps(
                        {
                            "start": catchup_from.isoformat(),
                            "end": timestamp,
                            "maximum_hours": 72,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    source_url=None,
                    as_of_time=current,
                    priority=2,
                    requested_at=current,
                )
            else:
                connection.execute(
                    """
                    UPDATE industry_chain_runtime_control
                    SET enabled = 0, state = 'pausing', paused_at = ?,
                        catchup_from = NULL, updated_at = ?
                    WHERE id = 1
                    """,
                    (timestamp, timestamp),
                )
                connection.execute(
                    """
                    UPDATE industry_chain_hunting_requests
                    SET status = 'paused'
                    WHERE status = 'queued'
                    """
                )
    return read_runtime_status(path, now=current)


def complete_hunting_pause(
    path: Path,
    *,
    now: datetime | None = None,
) -> RuntimeStatus:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    timestamp = current.isoformat()
    with open_database_connection(path) as connection:
        with connection:
            connection.execute(
                """
                UPDATE industry_chain_hunting_requests
                SET status = 'paused'
                WHERE status = 'queued'
                """
            )
            connection.execute(
                """
                UPDATE industry_chain_runtime_control
                SET state = 'paused', worker_heartbeat_at = ?, updated_at = ?
                WHERE id = 1 AND enabled = 0
                """,
                (timestamp, timestamp),
            )
    return read_runtime_status(path, now=current)


def submit_manual_hunt(
    path: Path,
    *,
    trigger_type: TriggerType,
    content: str,
    now: datetime | None = None,
) -> HuntingRequestRecord:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    normalized = content.strip()
    if not normalized:
        raise ValueError("手动狩猎内容不能为空")
    if len(normalized) > 10_000:
        raise ValueError("手动狩猎内容不能超过 10000 个字符")
    source_url: str | None = None
    if trigger_type == "url":
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("网址必须是有效的公开 HTTP 或 HTTPS 地址")
        if parsed.username or parsed.password:
            raise ValueError("网址不能包含账号或密码")
        source_url = normalized
    with open_database_connection(path) as connection:
        enabled_row = connection.execute(
            "SELECT enabled FROM industry_chain_runtime_control WHERE id = 1"
        ).fetchone()
        if enabled_row != (1,):
            raise HuntingDisabledError("产业链狩猎服务已关闭，请先开启")
        with connection:
            request_id = _insert_request(
                connection,
                trigger_method="manual",
                trigger_type=trigger_type,
                trigger_content=normalized,
                source_url=source_url,
                as_of_time=current,
                priority=1,
                requested_at=current,
            )
    request = read_hunting_request(path, request_id)
    if request is None:
        raise RuntimeError("手动狩猎请求写入失败")
    return request


def read_hunting_requests(
    path: Path,
    *,
    limit: int = 20,
) -> tuple[HuntingRequestRecord, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT request_id, trigger_method, trigger_type, trigger_content,
                   source_url, as_of_time, priority, status, requested_at,
                   started_at, completed_at, error_summary
            FROM industry_chain_hunting_requests
            ORDER BY requested_at DESC, request_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return tuple(_request_from_row(row) for row in rows)


def read_hunting_request(
    path: Path,
    request_id: str,
) -> HuntingRequestRecord | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT request_id, trigger_method, trigger_type, trigger_content,
                   source_url, as_of_time, priority, status, requested_at,
                   started_at, completed_at, error_summary
            FROM industry_chain_hunting_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    return _request_from_row(row) if row is not None else None


def record_worker_heartbeat(
    path: Path,
    *,
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    with open_database_connection(path) as connection:
        connection.execute(
            """
            UPDATE industry_chain_runtime_control
            SET worker_heartbeat_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (current.isoformat(), current.isoformat()),
        )


def record_runtime_activity(
    path: Path,
    *,
    now: datetime,
    polled: bool = False,
    model_ran: bool = False,
    error_summary: str | None = None,
) -> None:
    current = now.astimezone(BEIJING)
    assignments = [
        "worker_heartbeat_at = ?",
        "error_summary = ?",
        "updated_at = ?",
    ]
    values: list[object] = [
        current.isoformat(),
        error_summary,
        current.isoformat(),
    ]
    if polled:
        assignments.append("last_poll_at = ?")
        values.append(current.isoformat())
    if model_ran:
        assignments.append("last_model_run_at = ?")
        values.append(current.isoformat())
    values.append(1)
    with open_database_connection(path) as connection:
        connection.execute(
            f"""
            UPDATE industry_chain_runtime_control
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            values,
        )


def record_cleanup_completed(
    path: Path,
    *,
    now: datetime,
) -> None:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        connection.execute(
            """
            UPDATE industry_chain_runtime_control
            SET last_cleanup_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (current.isoformat(), current.isoformat()),
        )


def claim_next_manual_hunt(
    path: Path,
    *,
    now: datetime,
) -> HuntingRequestRecord | None:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        with connection:
            row = connection.execute(
                """
                SELECT request_id
                FROM industry_chain_hunting_requests
                WHERE status = 'queued'
                  AND trigger_method = 'manual'
                  AND trigger_type IN ('url', 'message', 'keyword')
                ORDER BY priority, requested_at, request_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            request_id = cast(str, row[0])
            cursor = connection.execute(
                """
                UPDATE industry_chain_hunting_requests
                SET status = 'running', started_at = ?, error_summary = NULL
                WHERE request_id = ? AND status = 'queued'
                """,
                (current.isoformat(), request_id),
            )
            if cursor.rowcount != 1:
                return None
    return read_hunting_request(path, request_id)


def claim_next_source_event_hunt(
    path: Path,
    *,
    now: datetime,
) -> HuntingRequestRecord | None:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        with connection:
            connection.execute(
                """
                UPDATE industry_chain_hunting_requests
                SET status = 'cancelled', completed_at = ?,
                    error_summary = '超过72小时新闻保鲜窗口，停止深挖'
                WHERE status = 'queued'
                  AND trigger_method = 'new_evidence'
                  AND trigger_type = 'source_event'
                  AND as_of_time < ?
                """,
                (
                    current.isoformat(),
                    (current - NEWS_FRESHNESS).isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT request_id
                FROM industry_chain_hunting_requests
                WHERE status = 'queued'
                  AND trigger_method = 'new_evidence'
                  AND trigger_type = 'source_event'
                ORDER BY priority, requested_at, request_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            request_id = cast(str, row[0])
            cursor = connection.execute(
                """
                UPDATE industry_chain_hunting_requests
                SET status = 'running', started_at = ?, error_summary = NULL
                WHERE request_id = ? AND status = 'queued'
                """,
                (current.isoformat(), request_id),
            )
            if cursor.rowcount != 1:
                return None
    return read_hunting_request(path, request_id)


def finish_hunting_request(
    path: Path,
    *,
    request_id: str,
    succeeded: bool,
    now: datetime,
    error_summary: str | None = None,
) -> HuntingRequestRecord:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            """
            UPDATE industry_chain_hunting_requests
            SET status = ?, completed_at = ?, error_summary = ?
            WHERE request_id = ? AND status = 'running'
            """,
            (
                "succeeded" if succeeded else "failed",
                current.isoformat(),
                None if succeeded else (error_summary or "执行失败")[:1000],
                request_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("狩猎请求不在可完成状态")
    request = read_hunting_request(path, request_id)
    if request is None:
        raise RuntimeError("狩猎请求完成后无法读取")
    return request


def recover_interrupted_hunts(
    path: Path,
    *,
    now: datetime,
) -> tuple[int, int]:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        with connection:
            completed = connection.execute(
                """
                UPDATE industry_chain_hunting_requests AS request
                SET status = 'succeeded', completed_at = ?,
                    error_summary = NULL
                WHERE request.status = 'running'
                  AND EXISTS (
                      SELECT 1
                      FROM industry_chain_selections AS selection
                      WHERE selection.selection_id =
                            'ics-' || request.request_id
                  )
                """,
                (current.isoformat(),),
            ).rowcount
            requeued = connection.execute(
                """
                UPDATE industry_chain_hunting_requests
                SET status = 'queued', started_at = NULL,
                    error_summary = '后台进程中断，已自动重新排队'
                WHERE status = 'running'
                """
            ).rowcount
    return completed, requeued


def finish_catchup_requests(
    path: Path,
    *,
    now: datetime,
) -> int:
    current = now.astimezone(BEIJING)
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            """
            UPDATE industry_chain_hunting_requests
            SET status = 'succeeded', started_at = COALESCE(started_at, ?),
                completed_at = ?, error_summary = NULL
            WHERE status = 'queued'
              AND trigger_method = 'resume_catchup'
            """,
            (current.isoformat(), current.isoformat()),
        )
    return cursor.rowcount


def enqueue_source_event_hunt(
    path: Path,
    *,
    discovery_ids: tuple[str, ...],
    event_types: tuple[str, ...],
    priority: int,
    now: datetime,
    event_cluster_id: str | None = None,
    cluster_evidence_count: int | None = None,
) -> HuntingRequestRecord:
    if not discovery_ids:
        raise ValueError("来源事件至少需要一个发现项")
    with open_database_connection(path) as connection:
        with connection:
            request_id = _insert_request(
                connection,
                trigger_method="new_evidence",
                trigger_type="source_event",
                trigger_content=json.dumps(
                    {
                        "discovery_ids": discovery_ids,
                        "suspected_event_types": event_types,
                        **(
                            {
                                "event_cluster_id": event_cluster_id,
                                "cluster_evidence_count": (
                                    cluster_evidence_count
                                ),
                            }
                            if event_cluster_id is not None
                            else {}
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                source_url=None,
                as_of_time=now,
                priority=priority,
                requested_at=now,
            )
    request = read_hunting_request(path, request_id)
    if request is None:
        raise RuntimeError("来源事件狩猎请求写入失败")
    return request


def next_scheduled_scan(now: datetime) -> datetime:
    current = now.astimezone(BEIJING)
    for day_offset in range(8):
        candidate_date = current.date() + timedelta(days=day_offset)
        schedule = (
            TRADING_DAY_SCANS
            if candidate_date.weekday() < 5
            else NON_TRADING_DAY_SCANS
        )
        for scheduled_time in schedule:
            candidate = datetime.combine(
                candidate_date,
                scheduled_time,
                tzinfo=BEIJING,
            )
            if candidate > current:
                return candidate
    raise RuntimeError("无法计算下一次产业链巡检时间")


def _insert_request(
    connection: sqlite3.Connection,
    *,
    trigger_method: str,
    trigger_type: str,
    trigger_content: str,
    source_url: str | None,
    as_of_time: datetime,
    priority: int,
    requested_at: datetime,
) -> str:
    request_id = f"ich-{requested_at:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:12]}"
    connection.execute(
        """
        INSERT INTO industry_chain_hunting_requests (
            request_id, trigger_method, trigger_type, trigger_content,
            source_url, as_of_time, priority, status, requested_at,
            started_at, completed_at, error_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, NULL, NULL, NULL)
        """,
        (
            request_id,
            trigger_method,
            trigger_type,
            trigger_content,
            source_url,
            as_of_time.isoformat(),
            priority,
            requested_at.isoformat(),
        ),
    )
    return request_id


def _request_from_row(row: tuple[object, ...]) -> HuntingRequestRecord:
    return HuntingRequestRecord(
        request_id=cast(str, row[0]),
        trigger_method=cast(str, row[1]),
        trigger_type=cast(str, row[2]),
        trigger_content=cast(str, row[3]),
        source_url=cast(str | None, row[4]),
        as_of_time=datetime.fromisoformat(cast(str, row[5])),
        priority=cast(int, row[6]),
        status=cast(str, row[7]),
        requested_at=datetime.fromisoformat(cast(str, row[8])),
        started_at=_optional_datetime(row[9]),
        completed_at=_optional_datetime(row[10]),
        error_summary=cast(str | None, row[11]),
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(cast(str, value))
