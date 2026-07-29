from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from fourseasquant.main import app


def test_local_api_controls_hunting_and_queues_manual_request(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "industry-chain-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        initial = client.get("/api/industry-chain/status")
        disabled_hunt = client.post(
            "/api/industry-chain/hunts",
            json={"trigger_type": "keyword", "content": "铜精矿供应中断"},
        )
        enabled = client.put(
            "/api/industry-chain/control",
            json={"enabled": True},
        )
        hunt = client.post(
            "/api/industry-chain/hunts",
            json={"trigger_type": "keyword", "content": "铜精矿供应中断"},
        )
        history = client.get("/api/industry-chain/hunts?limit=10")
        sources = client.get("/api/industry-chain/sources")
        snapshot = {
            "selection_id": "selection-api-001",
            "selection_version": 1,
            "rules_version": "industry-chain-leader-spec-v0.4",
            "event": {"title": "接口删除测试"},
        }
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                INSERT INTO industry_chain_selections (
                    selection_id, selection_version, run_id, event_id,
                    event_version, status, candidate_count,
                    evidence_refs_json, snapshot_sha256, snapshot_json,
                    published_at
                ) VALUES (
                    'selection-api-001', 1, 'run-api-001',
                    'event-api-001', 1, 'selected', 1, '[]', ?, ?, ?
                )
                """,
                (
                    "a" * 64,
                    json.dumps(snapshot, ensure_ascii=False),
                    "2026-07-26T12:00:00+08:00",
                ),
            )
        selections_before_delete = client.get(
            "/api/industry-chain/selections?limit=10"
        )
        deleted = client.delete(
            "/api/industry-chain/selections/selection-api-001/1"
        )
        deleted_again = client.delete(
            "/api/industry-chain/selections/selection-api-001/1"
        )
        selections_after_delete = client.get(
            "/api/industry-chain/selections?limit=10"
        )

    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert disabled_hunt.status_code == 409
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["worker_online"] is False
    assert hunt.status_code == 201
    assert hunt.json()["status"] == "queued"
    assert hunt.json()["trigger_content"] == "铜精矿供应中断"
    assert history.status_code == 200
    assert len(history.json()) == 2
    assert sources.status_code == 200
    assert len(sources.json()) == 14
    assert selections_before_delete.status_code == 200
    assert selections_before_delete.json() == [snapshot]
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted_again.status_code == 200
    assert selections_after_delete.json() == []
