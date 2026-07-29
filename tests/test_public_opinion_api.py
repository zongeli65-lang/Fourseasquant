from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from fourseasquant.main import app
from fourseasquant.public_opinion import (
    BEIJING,
    ModelOpinionClassification,
    OpinionContent,
    aggregate_public_opinion,
)
from fourseasquant.public_opinion_repository import save_public_opinion_day
from fourseasquant.public_opinion_worker import PublicOpinionBatchController


def test_public_opinion_api_manages_watchlist_without_removed_source_endpoint(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        empty_overview = client.get("/api/public-opinion/overview")
        added = client.post(
            "/api/public-opinion/watchlist",
            json={"code": "000001", "name": "平安银行"},
        )
        overview = client.get("/api/public-opinion/overview")
        removed_source = client.post(
            "/api/public-opinion/xueqiu/jobs",
            json={
                "code": "000001",
                "start_date": "2026-01-01",
                "end_date": "2026-03-15",
            },
        )
        removed = client.delete("/api/public-opinion/watchlist/000001")
        inactive_overview = client.get("/api/public-opinion/overview")

    assert empty_overview.status_code == 200
    assert empty_overview.json() == {"items": []}
    assert added.status_code == 201
    assert added.json()["code"] == "000001"
    assert added.json()["active"] is True
    assert overview.status_code == 200
    assert overview.json()["items"][0]["entry_reason"] == "explicit_watchlist"
    assert overview.json()["items"][0]["monitoring_active"] is True

    assert removed_source.status_code == 405

    assert removed.status_code == 200
    assert removed.json()["active"] is False
    assert inactive_overview.status_code == 200
    assert inactive_overview.json()["items"][0]["monitoring_active"] is False


def test_public_opinion_api_rejects_invalid_watchlist_code(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-invalid-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        bad_code = client.post(
            "/api/public-opinion/watchlist",
            json={"code": "1", "name": "无效代码"},
        )
    assert bad_code.status_code == 422


def test_public_opinion_api_schedules_only_published_strategy_targets(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-schedule-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        client.post(
            "/api/public-opinion/watchlist",
            json={"code": "000001", "name": "平安银行"},
        )
        without_targets = client.post(
            "/api/public-opinion/schedule",
            json={"actual_date": "2026-07-26"},
        )
        targets = client.put(
            "/api/public-opinion/strategy-targets",
            json={
                "actual_date": "2026-07-26",
                "strategy_version": "core-strategy-v1",
                "codes": ["600000", "000001"],
            },
        )
        scheduled = client.post(
            "/api/public-opinion/schedule",
            json={"actual_date": "2026-07-26"},
        )

    assert without_targets.status_code == 201
    assert without_targets.json() == []
    assert targets.status_code == 200
    assert targets.json()["codes"] == ["600000", "000001"]
    assert scheduled.status_code == 201
    assert [job["code"] for job in scheduled.json()] == [
        "600000",
        "000001",
    ]
    assert all(
        job["target_source"] == "strategy"
        for job in scheduled.json()
    )


def test_public_opinion_api_creates_manual_single_stock_investigation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-manual-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        response = client.post(
            "/api/public-opinion/manual-jobs",
            json={
                "code": "000001",
                "start_date": "2026-07-25",
                "end_date": "2026-07-27",
            },
        )

    assert response.status_code == 201
    assert len(response.json()) == 1
    assert response.json()[0]["trigger"] == "manual"
    assert response.json()[0]["target_source"] == "manual"


def test_public_opinion_api_controls_backend_batch_without_browser(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-batch-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    monkeypatch.setattr(
        "fourseasquant.public_opinion_api.batch_controller",
        PublicOpinionBatchController(),
    )

    with TestClient(app) as client:
        initial = client.get("/api/public-opinion/jobs/batch-status")
        started = client.post("/api/public-opinion/jobs/run-batch")
        stopped = client.post("/api/public-opinion/jobs/stop-batch")

    assert initial.status_code == 200
    assert initial.json()["running"] is False
    assert started.status_code == 202
    assert started.json()["running"] is False
    assert started.json()["remaining"] == 0
    assert stopped.status_code == 200
    assert stopped.json()["stop_requested"] is True


def test_public_opinion_api_accepts_runtime_key_without_returning_secret(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-runtime-key-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "fourseasquant.public_opinion_api.batch_controller",
        PublicOpinionBatchController(),
    )
    secret = "sk-local-browser-test-secret"

    with TestClient(app) as client:
        initial = client.get("/api/public-opinion/api-configuration")
        configured = client.put(
            "/api/public-opinion/api-configuration",
            json={"api_key": secret},
        )
        batch_status = client.get("/api/public-opinion/jobs/batch-status")
        cleared = client.delete("/api/public-opinion/api-configuration")

    assert initial.status_code == 200
    assert initial.json() == {
        "configured": False,
        "source": "unconfigured",
        "transient": True,
        "updated_at": None,
    }
    assert configured.status_code == 200
    assert configured.json()["configured"] is True
    assert configured.json()["source"] == "runtime"
    assert configured.json()["transient"] is True
    assert secret not in configured.text
    assert "api_key" not in configured.json()
    assert batch_status.json()["classifier_configured"] is True
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False


def test_public_opinion_api_does_not_collect_without_deepseek_key(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    database = tmp_path / "public-opinion-no-key-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "fourseasquant.public_opinion_api.batch_controller",
        PublicOpinionBatchController(),
    )

    with TestClient(app) as client:
        client.put(
            "/api/public-opinion/strategy-targets",
            json={
                "actual_date": "2026-07-27",
                "strategy_version": "core-strategy-v1",
                "codes": ["000001"],
            },
        )
        jobs = client.post(
            "/api/public-opinion/schedule",
            json={"actual_date": "2026-07-27"},
        ).json()
        result = client.post(
            f"/api/public-opinion/jobs/{jobs[0]['id']}/run"
        )

    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["cursor"] is None
    assert "DEEPSEEK_API_KEY" in result.json()["error_summary"]


def test_public_opinion_api_exposes_separate_today_three_and_seven_day_windows(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from datetime import date, datetime

    database = tmp_path / "public-opinion-windows-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    monkeypatch.setenv("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "0")

    with TestClient(app) as client:
        for day in (24, 25, 26):
            actual_date = date(2026, 7, day)
            contents = [
                OpinionContent(
                    platform="eastmoney",
                    content_id=f"{day}-{index}",
                    code="000001",
                    url=f"https://example.test/{day}/{index}",
                    published_at=datetime(
                        2026, 7, day, 10, index, tzinfo=BEIJING
                    ),
                    collected_at=datetime(
                        2026, 7, 26, 20, tzinfo=BEIJING
                    ),
                    likes=0,
                    content_kind="topic",
                    source_type="user_original",
                    text="盈利增长，明确利好",
                )
                for index in range(10)
            ]
            aggregate = aggregate_public_opinion(
                contents,
                classifications={
                    content.content_id: ModelOpinionClassification(
                        content_id=content.content_id,
                        label="favorable",
                        confidence=0.95,
                        model="deepseek-v4-pro",
                        prompt_version="public-opinion-deepseek-v1",
                    )
                    for content in contents
                },
                actual_date=actual_date,
                platform="eastmoney",
                code="000001",
                collection_complete=True,
                rules_version="public-opinion-deepseek-v1",
            )
            save_public_opinion_day(
                database,
                contents=contents,
                classifications={
                    content.content_id: ModelOpinionClassification(
                        content_id=content.content_id,
                        label="favorable",
                        confidence=0.95,
                        model="deepseek-v4-pro",
                        prompt_version="public-opinion-deepseek-v1",
                    )
                    for content in contents
                },
                aggregate=aggregate,
                collected_at=datetime(2026, 7, 26, 20, tzinfo=BEIJING),
            )

        response = client.get(
            "/api/public-opinion/securities/000001/windows",
            params={"end_date": "2026-07-26"},
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["platform"] for item in body["platforms"]] == [
        "eastmoney",
        "sina",
        "tonghuashun",
    ]
    eastmoney = next(
        item for item in body["platforms"]
        if item["platform"] == "eastmoney"
    )
    assert eastmoney["today"]["direction"] == "favorable"
    assert eastmoney["three_day"]["direction"] == "favorable"
    assert eastmoney["seven_day"]["direction_status"] == "collecting"
