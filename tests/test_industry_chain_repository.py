from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain import (
    FutureEvidenceError,
    ImmutableRecordConflict,
    IndustryChainRepository,
    IndustryChainValidationError,
)


BEIJING = ZoneInfo("Asia/Shanghai")
AS_OF_TIME = datetime(2026, 7, 25, 10, 0, tzinfo=BEIJING)


def _evidence(
    *,
    evidence_id: str = "evidence-001",
    event_id: str = "event-001",
    published_at: str = "2026-07-25T09:30:00+08:00",
    headline: str = "示例产品供给收缩",
) -> dict[str, object]:
    return {
        "schema_version": "industry-chain-evidence-v0.1",
        "evidence_id": evidence_id,
        "event_id": event_id,
        "source": {
            "source_id": "official-example",
            "source_name": "示例官方来源",
            "source_tier": 1,
            "source_type": "government",
            "url": "https://example.test/official/event-001",
            "is_primary": True,
            "access_class": "public_no_login",
        },
        "headline": headline,
        "published_at": published_at,
        "updated_at": None,
        "collected_at": "2026-07-25T09:35:00+08:00",
        "content_sha256": "a" * 64,
        "language": "zh-CN",
        "is_reprint": False,
        "reprint_cluster_id": None,
        "retention_mode": "official_original",
        "original_file_path": None,
        "facts": [
            {
                "fact_id": "fact-001",
                "fact_type": "supply_contraction",
                "subject": "示例产品",
                "statement": "可用供给减少",
                "disclosure_mode": "quantitative",
                "numeric_value": 20,
                "unit": "%",
                "period": "2026-07",
                "excerpt": "示例产品可用供给减少百分之二十。",
                "retention_reason": "直接证明供给收缩",
            }
        ],
    }


def _repository(tmp_path: Path) -> tuple[Path, IndustryChainRepository]:
    database = tmp_path / "industry-chain-repository.db"
    initialize_database(database)
    return database, IndustryChainRepository(database)


def test_append_evidence_is_idempotent_and_readable(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    snapshot = _evidence()

    first = repository.append_evidence(snapshot, as_of_time=AS_OF_TIME)
    second = repository.append_evidence(snapshot, as_of_time=AS_OF_TIME)

    assert first.inserted is True
    assert second.inserted is False
    assert second.evidence_id == "evidence-001"
    assert repository.read_evidence("evidence-001") == snapshot


def test_same_immutable_evidence_id_cannot_change(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    repository.append_evidence(_evidence(), as_of_time=AS_OF_TIME)

    with pytest.raises(ImmutableRecordConflict, match="内容不同"):
        repository.append_evidence(
            _evidence(headline="试图改写标题"),
            as_of_time=AS_OF_TIME,
        )


def test_same_source_content_reuses_original_evidence_id(tmp_path: Path) -> None:
    database, repository = _repository(tmp_path)
    repository.append_evidence(_evidence(), as_of_time=AS_OF_TIME)

    duplicate = repository.append_evidence(
        _evidence(evidence_id="evidence-duplicate"),
        as_of_time=AS_OF_TIME,
    )

    assert duplicate.inserted is False
    assert duplicate.evidence_id == "evidence-001"
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_evidence"
        ).fetchone()
    assert count == (1,)


def test_same_source_content_can_be_linked_to_another_event(
    tmp_path: Path,
) -> None:
    database, repository = _repository(tmp_path)
    repository.append_evidence(_evidence(), as_of_time=AS_OF_TIME)

    duplicate = repository.append_evidence(
        _evidence(
            evidence_id="evidence-other-event",
            event_id="event-002",
        ),
        as_of_time=AS_OF_TIME,
    )

    assert duplicate.evidence_id == "evidence-001"
    with sqlite3.connect(database) as connection:
        links = connection.execute(
            """
            SELECT event_id
            FROM industry_chain_evidence_event_links
            WHERE evidence_id = 'evidence-001'
            ORDER BY event_id
            """
        ).fetchall()
    assert links == [("event-001",), ("event-002",)]


def test_future_evidence_is_rejected_before_database_write(
    tmp_path: Path,
) -> None:
    database, repository = _repository(tmp_path)

    with pytest.raises(FutureEvidenceError, match="之后"):
        repository.append_evidence(
            _evidence(published_at="2026-07-25T10:01:00+08:00"),
            as_of_time=AS_OF_TIME,
        )

    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_evidence"
        ).fetchone()
    assert count == (0,)


def test_unknown_evidence_field_is_rejected(tmp_path: Path) -> None:
    _, repository = _repository(tmp_path)
    snapshot = _evidence()
    snapshot["technical_score"] = 99

    with pytest.raises(IndustryChainValidationError, match="未知字段"):
        repository.append_evidence(snapshot, as_of_time=AS_OF_TIME)
