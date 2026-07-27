from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fourseasquant.main import app


def test_technical_score_endpoints_are_safe_before_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "technical-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))

    with TestClient(app) as client:
        status = client.get("/api/technical-scores/status")
        ranking = client.get(
            "/api/technical-scores/top",
            params={"target_date": date(2026, 7, 22).isoformat()},
        )
        full_page = client.get(
            "/api/technical-scores",
            params={
                "target_date": date(2026, 7, 22).isoformat(),
                "search": "600001",
            },
        )
        rejected_total_score = client.get(
            "/api/technical-scores",
            params={
                "target_date": date(2026, 7, 22).isoformat(),
                "sort_by": "total_score",
            },
        )
        rejected_turnover_ranking = client.get(
            "/api/technical-scores",
            params={
                "target_date": date(2026, 7, 22).isoformat(),
                "sort_by": "turnover_score",
            },
        )

    assert status.status_code == 200
    assert status.json()["status"] == "not_initialized"
    assert status.json()["publication"] is None
    assert ranking.status_code == 200
    assert ranking.json() == []
    assert full_page.status_code == 200
    assert full_page.json() == {
        "requested_date": "2026-07-22",
        "actual_data_date": None,
        "total": 0,
        "universe_count": 0,
        "current_score_count": 0,
        "stale_score_count": 0,
        "page": 1,
        "page_size": 100,
        "items": [],
    }
    assert rejected_total_score.status_code == 422
    assert rejected_turnover_ranking.status_code == 422


def test_sector_membership_contract_is_exposed_as_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "contract-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))

    with TestClient(app) as client:
        response = client.get("/api/contracts/sector-membership")

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "SectorMembershipSnapshot"
    assert "membership_version" in payload["properties"]
    assert "memberships" in payload["properties"]
