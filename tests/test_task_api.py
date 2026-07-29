from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from fourseasquant.database import (
    claim_automation_date,
    initialize_database,
    release_automation_date,
)
from fourseasquant.main import app


def test_manual_task_rejects_duplicate_target_date_run(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "task-api.db"
    initialize_database(database)
    target_date = date(2026, 7, 24)
    claim_id = claim_automation_date(
        database,
        target_date,
        datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    assert claim_id is not None
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_FAILURE_SIMULATION", "1")
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks/daily",
                json={"target_date": target_date.isoformat()},
            )
    finally:
        release_automation_date(database, target_date, claim_id)

    assert response.status_code == 409
    assert response.json() == {"detail": "该目标日期已有任务正在运行"}
