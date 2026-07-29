from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from fourseasquant.sqlite_connection import open_database_connection


DeepHuntOutcome = Literal[
    "invalid_event",
    "policy_only",
    "selected",
    "evidence_insufficient",
    "failed",
]


def record_deep_hunt_audit(
    path: Path,
    *,
    request_id: str,
    outcome: DeepHuntOutcome,
    decision: dict[str, object] | None,
    error_summary: str | None,
    model: str | None,
    prompt_version: str,
    started_at: datetime,
    completed_at: datetime,
) -> None:
    decision_json = (
        json.dumps(
            decision,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if decision is not None
        else None
    )
    with open_database_connection(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO industry_chain_deep_hunt_audits (
                    request_id, outcome, decision_json, error_summary,
                    model, prompt_version, started_at, completed_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    outcome,
                    decision_json,
                    error_summary,
                    model,
                    prompt_version,
                    started_at.isoformat(),
                    completed_at.isoformat(),
                    completed_at.isoformat(),
                ),
            )
