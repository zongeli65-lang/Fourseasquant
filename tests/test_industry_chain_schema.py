from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fourseasquant.database import database_is_ready, initialize_database


CORE_TABLES = {
    "industry_chain_runtime_control",
    "industry_chain_source_registry",
    "industry_chain_source_checkpoints",
    "industry_chain_discovery_items",
    "industry_chain_event_clusters",
    "industry_chain_event_cluster_discoveries",
    "industry_chain_hunting_requests",
    "industry_chain_stock_universes",
    "industry_chain_evidence",
    "industry_chain_evidence_event_links",
    "industry_chain_events",
    "industry_chain_selection_runs",
    "industry_chain_selections",
    "industry_chain_selection_dismissals",
    "industry_chain_deep_hunt_audits",
    "industry_chain_cleanup_runs",
    "industry_chain_notifications",
}


def test_database_initializes_industry_chain_core_tables(tmp_path: Path) -> None:
    database = tmp_path / "industry-chain.db"

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
        version = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert CORE_TABLES <= table_names
    assert version == ("32",)
    assert database_is_ready(database) is True


def test_industry_chain_evidence_is_immutable_at_database_level(
    tmp_path: Path,
) -> None:
    database = tmp_path / "immutable.db"
    initialize_database(database)
    values = (
        "evidence-001",
        "event-001",
        "cninfo",
        "巨潮资讯",
        1,
        "company_filing",
        "https://example.test/notice",
        1,
        "public_no_login",
        "主营业务公告",
        "2026-07-25T09:00:00+08:00",
        "2026-07-25T09:01:00+08:00",
        "2026-07-25T10:00:00+08:00",
        "a" * 64,
        "zh-CN",
        0,
        "official_original",
        "b" * 64,
        '{"schema_version":"industry-chain-evidence-v0.1"}',
        "2026-07-25T10:00:00+08:00",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_evidence (
                evidence_id, event_id, source_id, source_name, source_tier,
                source_type, source_url, is_primary, access_class, headline,
                published_at, collected_at, accepted_as_of_time,
                content_sha256, language, is_reprint, retention_mode,
                snapshot_sha256, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

        with pytest.raises(sqlite3.IntegrityError, match="不可修改"):
            connection.execute(
                """
                UPDATE industry_chain_evidence
                SET headline = '试图改写'
                WHERE evidence_id = 'evidence-001'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
            connection.execute(
                """
                DELETE FROM industry_chain_evidence
                WHERE evidence_id = 'evidence-001'
                """
            )
