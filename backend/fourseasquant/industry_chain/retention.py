from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fourseasquant.sqlite_connection import open_database_connection


RETENTION_POLICY_VERSION = "industry-chain-retention-v0.3"
DISCOVERY_RETENTION = timedelta(hours=72)
RUN_RETENTION = timedelta(days=30)


class SelectionNotFoundError(LookupError):
    """用户要移除的候选分析不存在。"""


@dataclass(frozen=True)
class SelectionDismissalResult:
    selection_id: str
    selection_version: int
    newly_dismissed: bool
    dismissed_at: datetime


@dataclass(frozen=True)
class CleanupResult:
    cleanup_run_id: str
    discovery_cutoff: datetime
    run_cutoff: datetime
    deleted_counts: dict[str, int]
    completed_at: datetime


def dismiss_selection(
    path: Path,
    *,
    selection_id: str,
    selection_version: int,
    now: datetime,
) -> SelectionDismissalResult:
    _require_aware(now)
    if selection_version < 1:
        raise ValueError("selection_version 必须大于零")
    with open_database_connection(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        exists = connection.execute(
            """
            SELECT 1
            FROM industry_chain_selections
            WHERE selection_id = ? AND selection_version = ?
            """,
            (selection_id, selection_version),
        ).fetchone()
        if exists is None:
            raise SelectionNotFoundError(
                f"候选分析不存在：{selection_id} v{selection_version}"
            )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_selection_dismissals (
                selection_id, selection_version, reason,
                dismissed_at, created_at
            ) VALUES (?, ?, 'user_deleted', ?, ?)
            """,
            (
                selection_id,
                selection_version,
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return SelectionDismissalResult(
        selection_id=selection_id,
        selection_version=selection_version,
        newly_dismissed=cursor.rowcount == 1,
        dismissed_at=now,
    )


def cleanup_runtime_data(
    path: Path,
    *,
    now: datetime,
    discovery_retention: timedelta = DISCOVERY_RETENTION,
    run_retention: timedelta = RUN_RETENTION,
) -> CleanupResult:
    _require_aware(now)
    if discovery_retention <= timedelta(0) or run_retention <= timedelta(0):
        raise ValueError("留存时间必须大于零")
    discovery_cutoff = now - discovery_retention
    run_cutoff = now - run_retention
    started_at = now
    cleanup_run_id = f"cleanup-{uuid.uuid4().hex}"
    with open_database_connection(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            deleted_counts = {
                "search_runs": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_search_runs
                    WHERE completed_at < ?
                    """,
                    (run_cutoff.isoformat(),),
                ),
                "hunting_requests": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_hunting_requests AS request
                    WHERE request.status IN (
                        'succeeded', 'failed', 'cancelled'
                    )
                      AND COALESCE(
                          request.completed_at, request.requested_at
                      ) < ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM industry_chain_search_runs AS search
                          WHERE search.request_id = request.request_id
                      )
                    """,
                    (run_cutoff.isoformat(),),
                ),
                "triage_runs": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_triage_runs AS triage
                    WHERE triage.completed_at < ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM json_each(
                                   triage.discovery_ids_json
                               ) AS triaged,
                               industry_chain_hunting_requests AS request,
                               json_each(
                                   CASE
                                       WHEN json_valid(
                                           request.trigger_content
                                       )
                                       THEN request.trigger_content
                                       ELSE '{}'
                                   END,
                                   '$.discovery_ids'
                               ) AS requested
                          WHERE request.status IN (
                              'queued', 'running', 'paused'
                          )
                            AND requested.value = triaged.value
                      )
                    """,
                    (discovery_cutoff.isoformat(),),
                ),
                "event_cluster_links": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_event_cluster_discoveries
                    WHERE discovery_id IN (
                        SELECT discovery.discovery_id
                        FROM industry_chain_discovery_items AS discovery
                        WHERE discovery.collected_at < ?
                          AND NOT EXISTS (
                              SELECT 1
                              FROM industry_chain_triage_runs AS triage,
                                   json_each(
                                       triage.discovery_ids_json
                                   ) AS triaged
                              WHERE triaged.value =
                                  discovery.discovery_id
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM industry_chain_hunting_requests AS request,
                                   json_each(
                                       CASE
                                           WHEN json_valid(
                                               request.trigger_content
                                           )
                                           THEN request.trigger_content
                                           ELSE '{}'
                                       END,
                                       '$.discovery_ids'
                                   ) AS requested
                              WHERE request.status IN (
                                  'queued', 'running', 'paused'
                              )
                                AND requested.value =
                                    discovery.discovery_id
                          )
                    )
                    """,
                    (discovery_cutoff.isoformat(),),
                ),
                "discovery_items": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_discovery_items AS discovery
                    WHERE discovery.collected_at < ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM industry_chain_triage_runs AS triage,
                               json_each(
                                   triage.discovery_ids_json
                               ) AS triaged
                          WHERE triaged.value = discovery.discovery_id
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM industry_chain_hunting_requests AS request,
                               json_each(
                                   CASE
                                       WHEN json_valid(
                                           request.trigger_content
                                       )
                                       THEN request.trigger_content
                                       ELSE '{}'
                                   END,
                                   '$.discovery_ids'
                               ) AS requested
                          WHERE request.status IN (
                              'queued', 'running', 'paused'
                          )
                            AND requested.value =
                                discovery.discovery_id
                      )
                    """,
                    (discovery_cutoff.isoformat(),),
                ),
                "event_clusters": _delete_count(
                    connection,
                    """
                    DELETE FROM industry_chain_event_clusters AS cluster
                    WHERE cluster.last_seen_at < ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM industry_chain_event_cluster_discoveries AS link
                          WHERE link.cluster_id = cluster.cluster_id
                      )
                    """,
                    (discovery_cutoff.isoformat(),),
                ),
            }
            connection.execute(
                """
                INSERT INTO industry_chain_cleanup_runs (
                    cleanup_run_id, policy_version, discovery_cutoff,
                    run_cutoff, deleted_counts_json, started_at,
                    completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleanup_run_id,
                    RETENTION_POLICY_VERSION,
                    discovery_cutoff.isoformat(),
                    run_cutoff.isoformat(),
                    json.dumps(
                        deleted_counts,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    started_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return CleanupResult(
        cleanup_run_id=cleanup_run_id,
        discovery_cutoff=discovery_cutoff,
        run_cutoff=run_cutoff,
        deleted_counts=deleted_counts,
        completed_at=now,
    )


def _delete_count(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[str, ...],
) -> int:
    cursor = connection.execute(sql, parameters)
    return cursor.rowcount


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")
