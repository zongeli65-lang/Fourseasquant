from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain import (
    FutureEvidenceError,
    ImmutableRecordConflict,
    IndustryChainRepository,
    MissingEvidenceError,
    UnknownStockUniverseError,
)


BEIJING = ZoneInfo("Asia/Shanghai")
AS_OF_TIME = datetime(2026, 7, 25, 10, 0, tzinfo=BEIJING)
EXAMPLE_PATH = (
    Path(__file__).parent.parent
    / "contracts/examples/industry-chain-selection.example.json"
)


def _selection() -> dict[str, object]:
    return cast(dict[str, object], json.loads(EXAMPLE_PATH.read_text()))


def _evidence(
    evidence_id: str,
    *,
    content_character: str,
    event_id: str = "event-example-supply-001",
    source_url: str | None = None,
    published_at: str = "2026-07-25T09:30:00+08:00",
    collected_at: str = "2026-07-25T09:35:00+08:00",
) -> dict[str, object]:
    return {
        "schema_version": "industry-chain-evidence-v0.1",
        "evidence_id": evidence_id,
        "event_id": event_id,
        "source": {
            "source_id": f"source-{evidence_id}",
            "source_name": "示例正式来源",
            "source_tier": 1,
            "source_type": "company_filing",
            "url": source_url or f"https://example.test/{evidence_id}",
            "is_primary": True,
            "access_class": "public_no_login",
        },
        "headline": f"{evidence_id} 标题",
        "published_at": published_at,
        "updated_at": None,
        "collected_at": collected_at,
        "content_sha256": content_character * 64,
        "language": "zh-CN",
        "is_reprint": False,
        "reprint_cluster_id": None,
        "retention_mode": "official_original",
        "original_file_path": None,
        "facts": [
            {
                "fact_id": f"fact-{evidence_id}",
                "fact_type": "supply_contraction",
                "subject": "示例材料",
                "statement": "正式披露的示例事实",
                "disclosure_mode": "official_qualitative",
                "numeric_value": None,
                "unit": None,
                "period": None,
                "excerpt": "示例正式证据片段。",
                "retention_reason": "支撑示例候选",
            }
        ],
    }


def _prepared_repository(
    tmp_path: Path,
) -> tuple[Path, IndustryChainRepository]:
    database = tmp_path / "selection.db"
    initialize_database(database)
    _insert_universe(database)
    repository = IndustryChainRepository(database)
    for evidence_id, character in (
        ("evidence-industry-example-001", "a"),
        ("evidence-company-example-001", "b"),
        ("evidence-company-example-002", "c"),
    ):
        repository.append_evidence(
            _evidence(evidence_id, content_character=character),
            as_of_time=AS_OF_TIME,
        )
    return database, repository


def _insert_universe(database: Path) -> None:
    payload = json.dumps(
        {
            "universe_version": "main-board-universe-example",
            "securities": [
                {
                    "code": "600001",
                    "name": "示例股份",
                    "listing_trading_days": 100,
                }
            ],
        }
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_stock_universes (
                universe_version, as_of_time, actual_data_date, source,
                source_published_at, eligibility_rules_version,
                minimum_listing_trading_days, security_count, content_sha256,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "main-board-universe-example",
                "2026-07-25T09:00:00+08:00",
                "2026-07-24",
                "test",
                "2026-07-25T09:00:00+08:00",
                "industry-chain-main-board-v1",
                60,
                1,
                "f" * 64,
                payload,
                "2026-07-25T09:00:00+08:00",
            ),
        )


def test_selection_publish_is_atomic_idempotent_and_readable(
    tmp_path: Path,
) -> None:
    database, repository = _prepared_repository(tmp_path)
    snapshot = _selection()

    first = repository.publish_selection(
        snapshot,
        source_config_version="industry-chain-sources-v0.1",
    )
    second = repository.publish_selection(
        snapshot,
        source_config_version="industry-chain-sources-v0.1",
    )

    assert first.inserted is True
    assert first.event_version == 1
    assert second.inserted is False
    assert repository.read_selection("ics-20260725-example-001") == snapshot
    with sqlite3.connect(database) as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "industry_chain_events",
                "industry_chain_selection_runs",
                "industry_chain_selections",
            )
        )
    assert counts == (1, 1, 1)


def test_selection_accepts_immutable_evidence_reused_by_another_event(
    tmp_path: Path,
) -> None:
    database = tmp_path / "selection-reused-evidence.db"
    initialize_database(database)
    _insert_universe(database)
    repository = IndustryChainRepository(database)
    for evidence_id, character in (
        ("evidence-industry-example-001", "a"),
        ("evidence-company-example-001", "b"),
        ("evidence-company-example-002", "c"),
    ):
        source_url = f"https://example.test/{evidence_id}"
        repository.append_evidence(
            _evidence(
                evidence_id,
                content_character=character,
                event_id="event-earlier-research",
                source_url=source_url,
            ),
            as_of_time=AS_OF_TIME,
        )
        linked = repository.append_evidence(
            _evidence(
                f"duplicate-{evidence_id}",
                content_character=character,
                source_url=source_url,
            ),
            as_of_time=AS_OF_TIME,
        )
        assert linked.evidence_id == evidence_id

    published = repository.publish_selection(
        _selection(),
        source_config_version="industry-chain-sources-v0.1",
    )

    assert published.inserted is True


def test_selection_cannot_be_rewritten(tmp_path: Path) -> None:
    _, repository = _prepared_repository(tmp_path)
    snapshot = _selection()
    repository.publish_selection(
        snapshot,
        source_config_version="industry-chain-sources-v0.1",
    )
    snapshot["summary"] = "试图改写历史结论"

    with pytest.raises(ImmutableRecordConflict, match="内容不同"):
        repository.publish_selection(
            snapshot,
            source_config_version="industry-chain-sources-v0.1",
        )


def test_missing_evidence_rolls_back_event_and_run(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    initialize_database(database)
    _insert_universe(database)
    repository = IndustryChainRepository(database)

    with pytest.raises(MissingEvidenceError, match="缺少证据"):
        repository.publish_selection(
            _selection(),
            source_config_version="industry-chain-sources-v0.1",
        )

    with sqlite3.connect(database) as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "industry_chain_events",
                "industry_chain_selection_runs",
                "industry_chain_selections",
            )
        )
    assert counts == (0, 0, 0)


def test_selection_cannot_reference_evidence_published_after_as_of_time(
    tmp_path: Path,
) -> None:
    _, repository = _prepared_repository(tmp_path)
    future = _evidence(
        "evidence-company-example-002",
        content_character="d",
        published_at="2026-07-25T10:30:00+08:00",
        collected_at="2026-07-25T10:35:00+08:00",
    )
    separate_database = tmp_path / "future.db"
    initialize_database(separate_database)
    _insert_universe(separate_database)
    repository = IndustryChainRepository(separate_database)
    for evidence_id, character in (
        ("evidence-industry-example-001", "a"),
        ("evidence-company-example-001", "b"),
    ):
        repository.append_evidence(
            _evidence(evidence_id, content_character=character),
            as_of_time=AS_OF_TIME,
        )
    repository.append_evidence(
        future,
        as_of_time=datetime(2026, 7, 25, 11, 0, tzinfo=BEIJING),
    )

    with pytest.raises(FutureEvidenceError, match="未来证据"):
        repository.publish_selection(
            _selection(),
            source_config_version="industry-chain-sources-v0.1",
        )


def test_selection_rejects_candidate_outside_frozen_universe(
    tmp_path: Path,
) -> None:
    _, repository = _prepared_repository(tmp_path)
    snapshot = _selection()
    candidates = cast(list[dict[str, object]], snapshot["candidates"])
    candidates[0]["code"] = "600002"

    with pytest.raises(UnknownStockUniverseError, match="不在冻结范围"):
        repository.publish_selection(
            snapshot,
            source_config_version="industry-chain-sources-v0.1",
        )


def test_selection_rejects_disclosed_business_share_below_threshold(
    tmp_path: Path,
) -> None:
    _, repository = _prepared_repository(tmp_path)
    snapshot = _selection()
    candidates = cast(list[dict[str, object]], snapshot["candidates"])
    business = cast(dict[str, object], candidates[0]["business_materiality"])
    business["disclosure_mode"] = "mixed"
    business["gross_profit_share"] = 0.19

    with pytest.raises(ValueError, match="低于 20%"):
        repository.publish_selection(
            snapshot,
            source_config_version="industry-chain-sources-v0.1",
        )


def test_selection_rejects_associate_without_material_holding_and_profit(
    tmp_path: Path,
) -> None:
    _, repository = _prepared_repository(tmp_path)
    snapshot = _selection()
    candidates = cast(list[dict[str, object]], snapshot["candidates"])
    ownership = cast(dict[str, object], candidates[0]["ownership_relation"])
    ownership.update(
        {
            "relation_type": "non_consolidated_associate",
            "holding_share": 0.1,
            "attributable_profit_share": 0.3,
        }
    )

    with pytest.raises(ValueError, match="低于 20%"):
        repository.publish_selection(
            snapshot,
            source_config_version="industry-chain-sources-v0.1",
        )


def test_selection_rejects_realization_beyond_one_quarter(
    tmp_path: Path,
) -> None:
    _, repository = _prepared_repository(tmp_path)
    snapshot = _selection()
    candidates = cast(list[dict[str, object]], snapshot["candidates"])
    realization = cast(dict[str, object], candidates[0]["realization"])
    realization.update(
        {
            "status": "within_one_quarter",
            "expected_start": "2027-01-01",
        }
    )

    with pytest.raises(ValueError, match="超过一个季度"):
        repository.publish_selection(
            snapshot,
            source_config_version="industry-chain-sources-v0.1",
        )
