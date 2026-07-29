from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.control import read_runtime_status
from fourseasquant.industry_chain.repository import read_recent_selections
from fourseasquant.industry_chain.retention import (
    cleanup_runtime_data,
    dismiss_selection,
)


BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)


def test_user_dismissal_hides_selection_but_preserves_audit_records(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dismissal.db"
    initialize_database(database)
    snapshot = {
        "selection_id": "selection-001",
        "selection_version": 1,
        "rules_version": "industry-chain-leader-spec-v0.4",
        "event": {"title": "示例供需事件"},
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_selections (
                selection_id, selection_version, run_id, event_id,
                event_version, status, candidate_count,
                evidence_refs_json, snapshot_sha256, snapshot_json,
                published_at
            ) VALUES (?, 1, ?, ?, 1, 'selected', 1, '[]', ?, ?, ?)
            """,
            (
                "selection-001",
                "run-001",
                "event-001",
                "a" * 64,
                json.dumps(snapshot, ensure_ascii=False),
                NOW.isoformat(),
            ),
        )

    assert read_recent_selections(database) == (snapshot,)
    assert read_runtime_status(database, now=NOW).published_count == 1

    first = dismiss_selection(
        database,
        selection_id="selection-001",
        selection_version=1,
        now=NOW,
    )
    second = dismiss_selection(
        database,
        selection_id="selection-001",
        selection_version=1,
        now=NOW,
    )

    assert first.newly_dismissed is True
    assert second.newly_dismissed is False
    assert read_recent_selections(database) == ()
    assert read_runtime_status(database, now=NOW).published_count == 0
    with sqlite3.connect(database) as connection:
        selection_count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_selections"
        ).fetchone()
        dismissal_count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_selection_dismissals"
        ).fetchone()
    assert selection_count == (1,)
    assert dismissal_count == (1,)


def test_cleanup_removes_expired_runtime_rows_and_preserves_live_or_formal_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cleanup.db"
    initialize_database(database)
    old_time = "2026-06-01T12:00:00+08:00"
    recent_time = "2026-07-25T12:00:00+08:00"
    discoveries = (
        ("expired", old_time, "https://example.test/expired"),
        ("formal", old_time, "https://example.test/formal"),
        ("active", old_time, "https://example.test/active"),
        ("recent", recent_time, "https://example.test/recent"),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for discovery_id, collected_at, source_url in discoveries:
            content_sha256 = hashlib.sha256(discovery_id.encode()).hexdigest()
            connection.execute(
                """
                INSERT INTO industry_chain_discovery_items (
                    discovery_id, source_id, external_id, headline,
                    published_at, collected_at, source_url,
                    content_sha256, payload_json, created_at
                ) VALUES (?, 'test-source', ?, ?, ?, ?, ?, ?, '{}', ?)
                """,
                (
                    discovery_id,
                    discovery_id,
                    discovery_id,
                    collected_at,
                    collected_at,
                    source_url,
                    content_sha256,
                    collected_at,
                ),
            )
        formal_sha256 = hashlib.sha256(b"formal").hexdigest()
        connection.execute(
            """
            INSERT INTO industry_chain_evidence (
                evidence_id, event_id, source_id, source_name,
                source_tier, source_type, source_url, is_primary,
                access_class, headline, published_at, collected_at,
                accepted_as_of_time, content_sha256, language,
                is_reprint, retention_mode, snapshot_sha256,
                snapshot_json, created_at
            ) VALUES (
                'evidence-formal', 'event-formal', 'test-source',
                '测试正式来源', 1, 'company_filing',
                'https://example.test/formal', 1, 'public_no_login',
                '正式证据', ?, ?, ?, ?, 'zh-CN', 0,
                'official_original', ?, '{}', ?
            )
            """,
            (
                old_time,
                old_time,
                old_time,
                formal_sha256,
                "e" * 64,
                old_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO industry_chain_triage_runs (
                triage_run_id, source_id, discovery_ids_json,
                decisions_json, model, prompt_version,
                model_metrics_json, input_sha256, started_at,
                completed_at, created_at
            ) VALUES (
                'triage-old', 'test-source', '["expired"]', '[]',
                'test-model', 'test-prompt', '{}', ?, ?, ?, ?
            )
            """,
            ("t" * 64, old_time, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO industry_chain_triage_runs (
                triage_run_id, source_id, discovery_ids_json,
                decisions_json, model, prompt_version,
                model_metrics_json, input_sha256, started_at,
                completed_at, created_at
            ) VALUES (
                'triage-active', 'test-source', '["active"]', '[]',
                'test-model', 'test-prompt', '{}', ?, ?, ?, ?
            )
            """,
            ("u" * 64, old_time, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO industry_chain_hunting_requests (
                request_id, trigger_method, trigger_type,
                trigger_content, as_of_time, priority, status,
                requested_at, started_at, completed_at
            ) VALUES (
                'hunt-old', 'manual', 'keyword', '旧深挖',
                ?, 1, 'succeeded', ?, ?, ?
            )
            """,
            (old_time, old_time, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO industry_chain_search_runs (
                search_run_id, request_id, input_text, plan_json,
                results_json, model, prompt_version, input_sha256,
                started_at, completed_at, created_at
            ) VALUES (
                'search-old', 'hunt-old', '旧搜索', '{}', '[]',
                'test-model', 'test-prompt', ?, ?, ?, ?
            )
            """,
            ("s" * 64, old_time, old_time, old_time),
        )
        connection.execute(
            """
            INSERT INTO industry_chain_hunting_requests (
                request_id, trigger_method, trigger_type,
                trigger_content, as_of_time, priority, status,
                requested_at
            ) VALUES (
                'hunt-active', 'new_evidence', 'source_event',
                '{"discovery_ids":["active"]}', ?, 1, 'queued', ?
            )
            """,
            (old_time, old_time),
        )

    result = cleanup_runtime_data(database, now=NOW)

    assert result.deleted_counts == {
        "search_runs": 1,
        "hunting_requests": 1,
        "triage_runs": 1,
        "event_cluster_links": 0,
        "discovery_items": 2,
        "event_clusters": 0,
    }
    with sqlite3.connect(database) as connection:
        remaining_discoveries = {
            row[0]
            for row in connection.execute(
                "SELECT discovery_id FROM industry_chain_discovery_items"
            )
        }
        remaining_hunts = {
            row[0]
            for row in connection.execute(
                "SELECT request_id FROM industry_chain_hunting_requests"
            )
        }
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM industry_chain_triage_runs),
                (SELECT COUNT(*) FROM industry_chain_search_runs),
                (SELECT COUNT(*) FROM industry_chain_evidence),
                (SELECT COUNT(*) FROM industry_chain_cleanup_runs)
            """
        ).fetchone()
    assert remaining_discoveries == {"active", "recent"}
    assert remaining_hunts == {"hunt-active"}
    assert counts == (1, 0, 1, 1)


def test_transient_rows_can_be_deleted_but_cannot_be_rewritten(
    tmp_path: Path,
) -> None:
    database = tmp_path / "transient-trigger.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_discovery_items (
                discovery_id, source_id, external_id, headline,
                published_at, collected_at, source_url,
                content_sha256, payload_json, created_at
            ) VALUES (
                'temporary', 'test-source', 'temporary', '临时线索',
                ?, ?, 'https://example.test/temporary', ?, '{}', ?
            )
            """,
            (
                NOW.isoformat(),
                NOW.isoformat(),
                "f" * 64,
                NOW.isoformat(),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="不可修改"):
            connection.execute(
                """
                UPDATE industry_chain_discovery_items
                SET headline = '试图改写'
                WHERE discovery_id = 'temporary'
                """
            )
        connection.execute(
            """
            DELETE FROM industry_chain_discovery_items
            WHERE discovery_id = 'temporary'
            """
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_discovery_items"
        ).fetchone()
    assert count == (0,)
