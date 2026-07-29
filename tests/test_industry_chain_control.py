from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.control import (
    claim_next_source_event_hunt,
    complete_hunting_pause,
    enqueue_source_event_hunt,
    HuntingDisabledError,
    next_scheduled_scan,
    read_hunting_requests,
    read_runtime_status,
    recover_interrupted_hunts,
    set_hunting_enabled,
    submit_manual_hunt,
)
from fourseasquant.industry_chain.source_registry import read_latest_sources


BEIJING = ZoneInfo("Asia/Shanghai")


def test_runtime_starts_disabled_and_seeds_approved_sources(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    initialize_database(database)

    status = read_runtime_status(database)
    sources = read_latest_sources(database)

    assert status.enabled is False
    assert status.state == "paused"
    assert status.worker_online is False
    assert status.discovered_count == 0
    assert status.triaged_discovery_count == 0
    assert status.untriaged_count == 0
    assert status.event_cluster_count == 0
    assert status.investigated_company_count == 0
    assert status.deep_hunt_count == 0
    assert status.published_count == 0
    assert len(sources) == 14
    assert {source.source_id for source in sources} >= {
        "cninfo",
        "sse",
        "szse",
        "stats-cn",
        "customs-cn",
        "miit-cn",
        "ndrc-cn",
        "mofcom-cn",
    }
    assert all(source.access_class == "public_no_login" for source in sources)
    assert all(source.lifecycle_state == "active" for source in sources)
    assert {
        "eastmoney-discovery",
        "ths-discovery",
        "cls-news",
        "xueqiu-social",
        "weibo-finance",
        "wechat-public",
    } <= {source.source_id for source in sources}


def test_source_upgrade_preserves_old_versions(tmp_path: Path) -> None:
    database = tmp_path / "source-history.db"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        eastmoney = connection.execute(
            """
            SELECT source_version, source_tier, lifecycle_state, config_version
            FROM industry_chain_source_registry
            WHERE source_id = 'eastmoney-discovery'
            ORDER BY source_version
            """
        ).fetchall()

    assert eastmoney == [
        (1, 4, "observing", "industry-chain-sources-v0.2"),
        (2, 3, "active", "industry-chain-sources-v0.3"),
        (3, 3, "active", "industry-chain-sources-v0.4"),
    ]


def test_enabling_after_long_pause_caps_catchup_at_72_hours(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catchup.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    paused_at = now - timedelta(days=10)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE industry_chain_runtime_control
            SET paused_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (paused_at.isoformat(), paused_at.isoformat()),
        )

    status = set_hunting_enabled(database, enabled=True, now=now)
    requests = read_hunting_requests(database)

    assert status.enabled is True
    assert status.state == "running"
    assert status.catchup_from == now - timedelta(hours=72)
    assert status.queued_count == 1
    assert len(requests) == 1
    assert requests[0].trigger_method == "resume_catchup"
    payload = json.loads(requests[0].trigger_content)
    assert payload["start"] == (now - timedelta(hours=72)).isoformat()
    assert payload["end"] == now.isoformat()


def test_manual_hunt_requires_enabled_service_and_pauses_safely(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)

    with pytest.raises(HuntingDisabledError, match="已关闭"):
        submit_manual_hunt(
            database,
            trigger_type="keyword",
            content="铜精矿供应中断",
            now=now,
        )

    set_hunting_enabled(database, enabled=True, now=now)
    request = submit_manual_hunt(
        database,
        trigger_type="url",
        content="https://www.cninfo.com.cn/example",
        now=now + timedelta(minutes=1),
    )
    paused = set_hunting_enabled(
        database,
        enabled=False,
        now=now + timedelta(minutes=2),
    )
    requests = read_hunting_requests(database)

    assert request.status == "queued"
    assert request.source_url == "https://www.cninfo.com.cn/example"
    assert paused.enabled is False
    assert paused.state == "pausing"
    assert {item.status for item in requests} == {"paused"}
    completed_pause = complete_hunting_pause(
        database,
        now=now + timedelta(minutes=3),
    )
    assert completed_pause.state == "paused"


def test_next_scan_uses_trading_and_non_trading_schedules() -> None:
    friday = datetime(2026, 7, 24, 21, 31, tzinfo=BEIJING)
    saturday = datetime(2026, 7, 25, 10, 1, tzinfo=BEIJING)

    assert next_scheduled_scan(friday) == datetime(
        2026,
        7,
        25,
        10,
        0,
        tzinfo=BEIJING,
    )
    assert next_scheduled_scan(saturday) == datetime(
        2026,
        7,
        25,
        21,
        0,
        tzinfo=BEIJING,
    )


def test_worker_restart_requeues_only_unfinished_running_hunts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "recover-running.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    with sqlite3.connect(database) as connection:
        for request_id in ("finished", "unfinished"):
            connection.execute(
                """
                INSERT INTO industry_chain_hunting_requests (
                    request_id, trigger_method, trigger_type,
                    trigger_content, as_of_time, priority, status,
                    requested_at, started_at
                ) VALUES (
                    ?, 'new_evidence', 'source_event',
                    '{"discovery_ids":["example"]}', ?, 1, 'running', ?, ?
                )
                """,
                (
                    request_id,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        connection.execute(
            """
            INSERT INTO industry_chain_selections (
                selection_id, selection_version, run_id, event_id,
                event_version, status, candidate_count,
                evidence_refs_json, snapshot_sha256, snapshot_json,
                published_at
            ) VALUES (
                'ics-finished', 1, 'run-finished', 'event-finished',
                1, 'selected', 1, '[]', ?, '{}', ?
            )
            """,
            ("a" * 64, now.isoformat()),
        )

    completed, requeued = recover_interrupted_hunts(
        database,
        now=now + timedelta(minutes=1),
    )
    statuses = {
        item.request_id: item.status
        for item in read_hunting_requests(database)
    }

    assert (completed, requeued) == (1, 1)
    assert statuses == {
        "finished": "succeeded",
        "unfinished": "queued",
    }


def test_source_event_older_than_freshness_window_is_cancelled(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stale-event.db"
    initialize_database(database)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    stale = now - timedelta(hours=73)
    enqueue_source_event_hunt(
        database,
        discovery_ids=("old-news",),
        event_types=("major_order",),
        priority=1,
        now=stale,
    )

    claimed = claim_next_source_event_hunt(database, now=now)
    requests = read_hunting_requests(database)

    assert claimed is None
    assert requests[0].status == "cancelled"
    assert requests[0].error_summary == "超过72小时新闻保鲜窗口，停止深挖"


def test_source_registry_history_is_immutable(tmp_path: Path) -> None:
    database = tmp_path / "sources.db"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="不可修改"):
            connection.execute(
                """
                UPDATE industry_chain_source_registry
                SET source_name = '试图改写'
                WHERE source_id = 'cninfo' AND source_version = 1
                """
            )
