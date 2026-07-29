from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .announcement_triage import TriageDecision
from .control import enqueue_source_event_hunt
from .discovery import DiscoveryItem, read_discovery_items
from .fresh_queue import NEWS_FRESHNESS


_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
_COMPANY_PREFIX = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9]{2,16})(?:[:：丨|｜])"
)
_NOISE = (
    "财联社",
    "东方财富",
    "同花顺",
    "公告",
    "快讯",
    "消息",
    "公司",
    "表示",
    "关于",
)


@dataclass(frozen=True)
class EventFunnelResult:
    cluster_count: int
    queued_count: int
    cluster_ids: tuple[str, ...]


def cluster_and_queue_actionable_events(
    path: Path,
    *,
    decisions: tuple[TriageDecision, ...],
    now: datetime,
) -> EventFunnelResult:
    """聚合重复报道，并仅为证据集合发生变化的事件建立深挖任务。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")
    if not decisions:
        return EventFunnelResult(0, 0, ())
    decision_by_id = {
        decision.discovery_id: decision for decision in decisions
    }
    items = read_discovery_items(path, tuple(decision_by_id))
    assignments: dict[str, list[TriageDecision]] = {}
    cutoff = now - NEWS_FRESHNESS
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            for item in items:
                decision = decision_by_id[item.discovery_id]
                cluster_id = _assign_cluster(
                    connection,
                    item=item,
                    decision=decision,
                    cutoff=cutoff,
                    now=now,
                )
                assignments.setdefault(cluster_id, []).append(decision)

    queued_count = 0
    for cluster_id, cluster_decisions in assignments.items():
        discovery_ids = _cluster_discovery_ids(path, cluster_id)
        if not discovery_ids or _already_requested(
            path,
            cluster_id=cluster_id,
            evidence_count=len(discovery_ids),
        ):
            continue
        ordered = sorted(
            cluster_decisions,
            key=lambda decision: (
                decision.priority,
                decision.discovery_id,
            ),
        )
        enqueue_source_event_hunt(
            path,
            discovery_ids=discovery_ids,
            event_types=tuple(
                dict.fromkeys(
                    decision.suspected_event_type
                    for decision in ordered
                )
            ),
            priority=_event_priority(ordered),
            now=now,
            event_cluster_id=cluster_id,
            cluster_evidence_count=len(discovery_ids),
        )
        queued_count += 1
    return EventFunnelResult(
        cluster_count=len(assignments),
        queued_count=queued_count,
        cluster_ids=tuple(assignments),
    )


def read_fresh_event_cluster_count(
    path: Path,
    *,
    as_of_time: datetime,
) -> int:
    cutoff = as_of_time - NEWS_FRESHNESS
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM industry_chain_event_clusters
            WHERE last_seen_at >= ? AND first_seen_at <= ?
            """,
            (cutoff.isoformat(), as_of_time.isoformat()),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def _assign_cluster(
    connection: sqlite3.Connection,
    *,
    item: DiscoveryItem,
    decision: TriageDecision,
    cutoff: datetime,
    now: datetime,
) -> str:
    normalized = _normalized_text(item)
    tokens = _tokens(normalized)
    headline_tokens = _tokens(_normalized_headline(item.headline))
    company_key = _company_key(item)
    rows = connection.execute(
        """
        SELECT cluster_id, token_json, company_key, canonical_headline
        FROM industry_chain_event_clusters
        WHERE last_seen_at >= ?
        ORDER BY last_seen_at DESC, cluster_id
        """,
        (cutoff.isoformat(),),
    ).fetchall()
    best_cluster: str | None = None
    best_score = 0.0
    for row in rows:
        existing_tokens = frozenset(json.loads(str(row[1])))
        score = max(
            _jaccard(tokens, existing_tokens),
            _jaccard(
                headline_tokens,
                _tokens(_normalized_headline(str(row[3]))),
            ),
        )
        same_company = bool(
            company_key
            and row[2]
            and company_key == str(row[2])
        )
        threshold = 0.35 if same_company else 0.52
        if score >= threshold and score > best_score:
            best_cluster = str(row[0])
            best_score = score
    if best_cluster is None:
        fingerprint = hashlib.sha256(
            "|".join(
                (
                    company_key or "",
                    decision.suspected_event_type,
                    normalized,
                )
            ).encode()
        ).hexdigest()
        best_cluster = f"icec-{fingerprint[:24]}"
        connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_event_clusters (
                cluster_id, event_fingerprint, suspected_event_type,
                canonical_headline, normalized_text, token_json,
                company_key, first_seen_at, last_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                best_cluster,
                fingerprint,
                decision.suspected_event_type,
                item.headline,
                normalized,
                json.dumps(sorted(tokens), ensure_ascii=False),
                company_key,
                item.published_at.isoformat(),
                item.published_at.isoformat(),
                now.isoformat(),
            ),
        )
    else:
        connection.execute(
            """
            UPDATE industry_chain_event_clusters
            SET last_seen_at = CASE
                WHEN last_seen_at < ? THEN ? ELSE last_seen_at END
            WHERE cluster_id = ?
            """,
            (
                item.published_at.isoformat(),
                item.published_at.isoformat(),
                best_cluster,
            ),
        )
    connection.execute(
        """
        INSERT OR IGNORE INTO industry_chain_event_cluster_discoveries (
            cluster_id, discovery_id, linked_at
        ) VALUES (?, ?, ?)
        """,
        (best_cluster, item.discovery_id, now.isoformat()),
    )
    return best_cluster


def _cluster_discovery_ids(path: Path, cluster_id: str) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT link.discovery_id
            FROM industry_chain_event_cluster_discoveries AS link
            JOIN industry_chain_discovery_items AS discovery
              ON discovery.discovery_id = link.discovery_id
            WHERE link.cluster_id = ?
            ORDER BY discovery.published_at, link.discovery_id
            """,
            (cluster_id,),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _already_requested(
    path: Path,
    *,
    cluster_id: str,
    evidence_count: int,
) -> bool:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM industry_chain_hunting_requests
            WHERE json_extract(
                    CASE
                        WHEN json_valid(trigger_content)
                        THEN trigger_content
                        ELSE '{}'
                    END,
                    '$.event_cluster_id'
                  ) = ?
              AND COALESCE(
                    json_extract(
                        CASE
                            WHEN json_valid(trigger_content)
                            THEN trigger_content
                            ELSE '{}'
                        END,
                        '$.cluster_evidence_count'
                    ),
                    0
                  ) >= ?
            LIMIT 1
            """,
            (cluster_id, evidence_count),
        ).fetchone()
    return row is not None


def _normalized_text(item: DiscoveryItem) -> str:
    content = str(
        item.payload.get("brief")
        or item.payload.get("snippet")
        or item.payload.get("content")
        or ""
    )
    text = "".join(_TEXT_PATTERN.findall(f"{item.headline}{content}"))
    return _remove_noise(text).lower()[:1_200]


def _normalized_headline(headline: str) -> str:
    text = "".join(_TEXT_PATTERN.findall(headline))
    return _remove_noise(text).lower()[:300]


def _remove_noise(text: str) -> str:
    for word in _NOISE:
        text = text.replace(word, "")
    return text


def _tokens(text: str) -> frozenset[str]:
    if len(text) < 2:
        return frozenset({text}) if text else frozenset()
    return frozenset(
        text[index : index + 2]
        for index in range(len(text) - 1)
    )


def _company_key(item: DiscoveryItem) -> str | None:
    if item.security_code:
        return item.security_code
    if item.security_name and len(item.security_name.strip()) >= 2:
        return item.security_name.strip()
    match = _COMPANY_PREFIX.match(item.headline.strip())
    return match.group(1) if match is not None else None


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


def _event_priority(decisions: list[TriageDecision]) -> int:
    floor_by_type = {
        "major_order": 1,
        "demand_growth": 1,
        "price_change": 1,
        "supply_contraction": 1,
        "inventory_change": 2,
        "capacity_expansion": 3,
        "policy_watch": 3,
        "uncertain": 3,
    }
    return max(
        min(decision.priority for decision in decisions),
        min(
            floor_by_type.get(decision.suspected_event_type, 3)
            for decision in decisions
        ),
    )
