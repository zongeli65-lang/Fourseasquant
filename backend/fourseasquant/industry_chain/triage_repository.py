from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .announcement_triage import AnnouncementTriageResult, TriageDecision
from .discovery import DiscoveryItem, read_discovery_items
from .fresh_queue import (
    MINIMUM_NEWS_VALUE_SCORE,
    NEWS_FRESHNESS,
    news_value_score,
)


TRIAGE_PROMPT_VERSION = "announcement-triage-v0.1"


def read_unclustered_actionable_decisions(
    path: Path,
    *,
    as_of_time: datetime,
    limit: int,
) -> tuple[TriageDecision, ...]:
    """读取尚未进入事件漏斗的近期可调查决定，优先真实需求与供给。"""
    if not 1 <= limit <= 100:
        raise ValueError("事件回填上限必须在 1 到 100 之间")
    cutoff = as_of_time - NEWS_FRESHNESS
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT decision.value
            FROM industry_chain_triage_runs AS triage,
                 json_each(
                     triage.decisions_json, '$.decisions'
                 ) AS decision
            JOIN industry_chain_discovery_items AS discovery
              ON discovery.discovery_id = json_extract(
                     decision.value, '$.discovery_id'
                 )
            WHERE json_extract(
                    decision.value, '$.action'
                  ) IN ('investigate', 'observe')
              AND discovery.published_at >= ?
              AND discovery.published_at <= ?
              AND json_extract(
                    discovery.payload_json,
                    '$.research_parent_request_id'
                  ) IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM industry_chain_event_cluster_discoveries AS link
                  WHERE link.discovery_id = discovery.discovery_id
              )
            ORDER BY
                CASE json_extract(
                    decision.value, '$.suspected_event_type'
                )
                    WHEN 'major_order' THEN 0
                    WHEN 'demand_growth' THEN 0
                    WHEN 'price_change' THEN 0
                    WHEN 'supply_contraction' THEN 0
                    WHEN 'inventory_change' THEN 1
                    WHEN 'capacity_expansion' THEN 2
                    WHEN 'policy_watch' THEN 3
                    ELSE 4
                END,
                json_extract(decision.value, '$.priority'),
                discovery.published_at DESC,
                discovery.discovery_id
            LIMIT ?
            """,
            (
                cutoff.isoformat(),
                as_of_time.isoformat(),
                100,
            ),
        ).fetchall()
    decisions = tuple(
        TriageDecision.model_validate(json.loads(str(row[0])))
        for row in rows
    )
    items = read_discovery_items(
        path,
        tuple(decision.discovery_id for decision in decisions),
    )
    score_by_id = {
        item.discovery_id: news_value_score(item) for item in items
    }
    return tuple(
        decision
        for decision in decisions
        if score_by_id.get(decision.discovery_id, -100)
        >= MINIMUM_NEWS_VALUE_SCORE
    )[:limit]


def read_untriaged_discovery_ids(
    path: Path,
    *,
    as_of_time: datetime,
    limit: int,
) -> tuple[str, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("未初筛发现读取上限必须在 1 到 100 之间")
    cutoff = as_of_time - NEWS_FRESHNESS
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT discovery.discovery_id
            FROM industry_chain_discovery_items AS discovery
            WHERE json_extract(
                    discovery.payload_json,
                    '$.research_parent_request_id'
                  ) IS NULL
              AND discovery.published_at >= ?
              AND discovery.published_at <= ?
              AND NOT EXISTS (
                SELECT 1
                FROM industry_chain_triage_runs AS triage,
                     json_each(triage.discovery_ids_json) AS triaged
                WHERE triaged.value = discovery.discovery_id
            )
            ORDER BY
                CASE
                    WHEN discovery.security_code IS NOT NULL THEN 0
                    ELSE 1
                END,
                CASE
                    WHEN discovery.headline LIKE '%重大订单%' THEN 0
                    WHEN discovery.headline LIKE '%签订%合同%' THEN 0
                    WHEN discovery.headline LIKE '%签订%订单%' THEN 0
                    WHEN discovery.headline LIKE '%中标%' THEN 0
                    WHEN discovery.headline LIKE '%批量供货%' THEN 0
                    WHEN discovery.headline LIKE '%批量交付%' THEN 0
                    WHEN discovery.headline LIKE '%暂停生产%' THEN 0
                    WHEN discovery.headline LIKE '%停产%' THEN 0
                    WHEN discovery.headline LIKE '%减产%' THEN 0
                    WHEN discovery.headline LIKE '%供货%' THEN 0
                    WHEN discovery.headline LIKE '%订单%' THEN 0
                    WHEN discovery.headline LIKE '%扩产%' THEN 0
                    WHEN discovery.headline LIKE '%投产%' THEN 0
                    WHEN discovery.headline LIKE '%供应%' THEN 0
                    WHEN discovery.headline LIKE '%需求%' THEN 0
                    WHEN discovery.headline LIKE '%库存%' THEN 0
                    WHEN discovery.headline LIKE '%涨价%' THEN 0
                    ELSE 1
                END,
                CASE
                    WHEN json_extract(
                        discovery.payload_json,
                        '$.published_at_known'
                    ) = 1 THEN 0
                    WHEN json_extract(
                        discovery.payload_json,
                        '$.publication_time_known'
                    ) = 1 THEN 0
                    ELSE 1
                END,
                discovery.published_at DESC,
                discovery.collected_at DESC,
                discovery.discovery_id
            LIMIT ?
            """,
            (
                cutoff.isoformat(),
                as_of_time.isoformat(),
                limit,
            ),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def save_announcement_triage(
    path: Path,
    *,
    items: tuple[DiscoveryItem, ...],
    result: AnnouncementTriageResult,
    started_at: datetime,
    completed_at: datetime,
) -> str:
    if not items:
        raise ValueError("不能保存空的公告初筛")
    input_json = json.dumps(
        [
            {
                "discovery_id": item.discovery_id,
                "content_sha256": item.content_sha256,
            }
            for item in items
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    input_sha256 = hashlib.sha256(input_json.encode()).hexdigest()
    triage_run_id = f"triage-{uuid.uuid4().hex}"
    decisions_json = result.batch.model_dump_json()
    metrics_json = json.dumps(
        {
            "total_duration_ns": result.model_result.total_duration_ns,
            "load_duration_ns": result.model_result.load_duration_ns,
            "prompt_eval_count": result.model_result.prompt_eval_count,
            "eval_count": result.model_result.eval_count,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    timestamp = completed_at.isoformat()
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_triage_runs (
                triage_run_id, source_id, discovery_ids_json, decisions_json,
                model, prompt_version, model_metrics_json, input_sha256,
                started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                triage_run_id,
                items[0].source_id,
                json.dumps(
                    [item.discovery_id for item in items],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                decisions_json,
                result.model_result.model,
                TRIAGE_PROMPT_VERSION,
                metrics_json,
                input_sha256,
                started_at.isoformat(),
                timestamp,
                timestamp,
            ),
        )
        if cursor.rowcount == 0:
            row = connection.execute(
                """
                SELECT triage_run_id
                FROM industry_chain_triage_runs
                WHERE source_id = ? AND input_sha256 = ?
                """,
                (items[0].source_id, input_sha256),
            ).fetchone()
            if row is None:
                raise RuntimeError("公告初筛记录写入失败")
            return str(row[0])
    return triage_run_id
