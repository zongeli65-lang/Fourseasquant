from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, cast

from fourseasquant.industry_chain.selection_validation import validate_selection
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.industry_chain.policy import (
    ACTIVE_SELECTION_RULES_VERSION,
)
from fourseasquant.industry_chain.validation import (
    FutureEvidenceError,
    validate_evidence,
)


class ImmutableRecordConflict(ValueError):
    """同一不可修改编号出现了不同内容。"""


class MissingEvidenceError(ValueError):
    """候选快照引用了尚未保存的证据。"""


class IneligibleEvidenceError(ValueError):
    """候选快照引用了不能支撑正式入选的来源。"""


class UnknownStockUniverseError(ValueError):
    """候选引用了未知、未来或不包含候选代码的股票范围。"""


@dataclass(frozen=True)
class AppendEvidenceResult:
    evidence_id: str
    inserted: bool


@dataclass(frozen=True)
class PublishSelectionResult:
    selection_id: str
    selection_version: int
    event_version: int
    inserted: bool


class IndustryChainRepository:
    """产业链证据和候选快照的唯一持久化入口。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append_evidence(
        self,
        snapshot: Mapping[str, object],
        *,
        as_of_time: datetime,
    ) -> AppendEvidenceResult:
        evidence = validate_evidence(snapshot, as_of_time=as_of_time)
        source = evidence.source
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT snapshot_sha256
                FROM industry_chain_evidence
                WHERE evidence_id = ?
                """,
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None:
                if cast(str, existing[0]) != evidence.snapshot_sha256:
                    raise ImmutableRecordConflict(
                        f"证据 {evidence.evidence_id} 已存在且内容不同"
                    )
                _link_evidence_to_event(
                    connection,
                    evidence_id=evidence.evidence_id,
                    event_id=evidence.event_id,
                    linked_at=as_of_time,
                )
                return AppendEvidenceResult(
                    evidence_id=evidence.evidence_id,
                    inserted=False,
                )
            duplicate = connection.execute(
                """
                SELECT evidence_id
                FROM industry_chain_evidence
                WHERE source_url = ? AND content_sha256 = ?
                """,
                (cast(str, source["url"]), evidence.content_sha256),
            ).fetchone()
            if duplicate is not None:
                duplicate_id = cast(str, duplicate[0])
                _link_evidence_to_event(
                    connection,
                    evidence_id=duplicate_id,
                    event_id=evidence.event_id,
                    linked_at=as_of_time,
                )
                return AppendEvidenceResult(
                    evidence_id=duplicate_id,
                    inserted=False,
                )
            connection.execute(
                """
                INSERT INTO industry_chain_evidence (
                    evidence_id, event_id, source_id, source_name, source_tier,
                    source_type, source_url, is_primary, access_class, headline,
                    published_at, updated_at, collected_at, accepted_as_of_time,
                    content_sha256, language, is_reprint, reprint_cluster_id,
                    retention_mode, original_file_path, snapshot_sha256,
                    snapshot_json, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    evidence.evidence_id,
                    evidence.event_id,
                    cast(str, source["source_id"]),
                    cast(str, source["source_name"]),
                    cast(int, source["source_tier"]),
                    cast(str, source["source_type"]),
                    cast(str, source["url"]),
                    int(cast(bool, source["is_primary"])),
                    cast(str, source["access_class"]),
                    evidence.headline,
                    evidence.published_at.isoformat(),
                    (
                        evidence.updated_at.isoformat()
                        if evidence.updated_at is not None
                        else None
                    ),
                    evidence.collected_at.isoformat(),
                    as_of_time.isoformat(),
                    evidence.content_sha256,
                    evidence.language,
                    int(evidence.is_reprint),
                    evidence.reprint_cluster_id,
                    evidence.retention_mode,
                    evidence.original_file_path,
                    evidence.snapshot_sha256,
                    evidence.snapshot_json,
                    evidence.collected_at.isoformat(),
                ),
            )
            _link_evidence_to_event(
                connection,
                evidence_id=evidence.evidence_id,
                event_id=evidence.event_id,
                linked_at=as_of_time,
            )
        return AppendEvidenceResult(
            evidence_id=evidence.evidence_id,
            inserted=True,
        )

    def read_evidence(self, evidence_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json
                FROM industry_chain_evidence
                WHERE evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
        if row is None:
            return None
        return cast(dict[str, object], json.loads(cast(str, row[0])))

    def publish_selection(
        self,
        snapshot: Mapping[str, object],
        *,
        source_config_version: str,
    ) -> PublishSelectionResult:
        selection = validate_selection(snapshot)
        if not source_config_version.strip():
            raise ValueError("source_config_version 不能为空")
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT snapshot_sha256, event_version
                FROM industry_chain_selections
                WHERE selection_id = ? AND selection_version = ?
                """,
                (selection.selection_id, selection.selection_version),
            ).fetchone()
            if existing is not None:
                if cast(str, existing[0]) != selection.snapshot_sha256:
                    raise ImmutableRecordConflict(
                        f"候选快照 {selection.run_id} 已存在且内容不同"
                    )
                return PublishSelectionResult(
                    selection_id=selection.selection_id,
                    selection_version=selection.selection_version,
                    event_version=cast(int, existing[1]),
                    inserted=False,
                )

            self._require_stock_universe(
                connection,
                universe_version=selection.stock_universe_version,
                candidate_codes=selection.candidate_codes,
                as_of_time=selection.as_of_time,
            )
            self._require_eligible_evidence(
                connection,
                evidence_refs=selection.evidence_refs,
                event_id=selection.event_id,
                as_of_time=selection.as_of_time,
                require_formal=selection.candidate_count > 0,
            )
            event_version = self._append_event(connection, selection)
            model_json = json.dumps(
                selection.model,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            input_sha256 = hashlib.sha256(
                (
                    selection.event_sha256
                    + "|"
                    + "|".join(selection.evidence_refs)
                    + "|"
                    + selection.rules_version
                    + "|"
                    + selection.stock_universe_version
                ).encode()
            ).hexdigest()
            with connection:
                connection.execute(
                    """
                    INSERT INTO industry_chain_selection_runs (
                        run_id, selection_id, selection_version, event_id,
                        event_version, trigger_method, as_of_time, rules_version,
                        model_json, prompt_version, knowledge_version,
                        stock_universe_version, source_config_version, status,
                        error_summary, started_at, completed_at, input_sha256,
                        created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?,
                        ?
                    )
                    """,
                    (
                        selection.run_id,
                        selection.selection_id,
                        selection.selection_version,
                        selection.event_id,
                        event_version,
                        selection.trigger_method,
                        selection.as_of_time.isoformat(),
                        selection.rules_version,
                        model_json,
                        selection.prompt_version,
                        selection.knowledge_version,
                        selection.stock_universe_version,
                        source_config_version,
                        selection.status,
                        selection.started_at.isoformat(),
                        selection.completed_at.isoformat(),
                        input_sha256,
                        selection.completed_at.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO industry_chain_selections (
                        selection_id, selection_version, run_id, event_id,
                        event_version, status, candidate_count,
                        evidence_refs_json, snapshot_sha256, snapshot_json,
                        published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        selection.selection_id,
                        selection.selection_version,
                        selection.run_id,
                        selection.event_id,
                        event_version,
                        selection.status,
                        selection.candidate_count,
                        json.dumps(selection.evidence_refs, separators=(",", ":")),
                        selection.snapshot_sha256,
                        selection.snapshot_json,
                        selection.completed_at.isoformat(),
                    ),
                )
        return PublishSelectionResult(
            selection_id=selection.selection_id,
            selection_version=selection.selection_version,
            event_version=event_version,
            inserted=True,
        )

    def read_selection(
        self,
        selection_id: str,
        *,
        selection_version: int | None = None,
    ) -> dict[str, object] | None:
        clause = ""
        parameters: tuple[object, ...] = (selection_id,)
        if selection_version is not None:
            clause = "AND selection_version = ?"
            parameters = (selection_id, selection_version)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT snapshot_json
                FROM industry_chain_selections
                WHERE selection_id = ? {clause}
                ORDER BY selection_version DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        if row is None:
            return None
        return cast(dict[str, object], json.loads(cast(str, row[0])))

    def _require_stock_universe(
        self,
        connection: sqlite3.Connection,
        *,
        universe_version: str,
        candidate_codes: tuple[str, ...],
        as_of_time: datetime,
    ) -> None:
        row = connection.execute(
            """
            SELECT as_of_time, payload_json
            FROM industry_chain_stock_universes
            WHERE universe_version = ?
            """,
            (universe_version,),
        ).fetchone()
        if row is None:
            raise UnknownStockUniverseError(
                f"股票范围版本不存在：{universe_version}"
            )
        if datetime.fromisoformat(cast(str, row[0])) > as_of_time:
            raise UnknownStockUniverseError("候选引用了 as_of_time 之后的股票范围")
        payload = cast(dict[str, object], json.loads(cast(str, row[1])))
        securities = cast(list[dict[str, object]], payload["securities"])
        eligible_codes = {cast(str, item["code"]) for item in securities}
        outside = sorted(set(candidate_codes) - eligible_codes)
        if outside:
            raise UnknownStockUniverseError(
                f"候选股票不在冻结范围：{outside}"
            )

    def _append_event(self, connection: sqlite3.Connection, selection: object) -> int:
        from fourseasquant.industry_chain.selection_validation import (
            ValidatedSelection,
        )

        validated = cast(ValidatedSelection, selection)
        existing = connection.execute(
            """
            SELECT event_version
            FROM industry_chain_events
            WHERE event_id = ? AND content_sha256 = ?
            """,
            (validated.event_id, validated.event_sha256),
        ).fetchone()
        if existing is not None:
            return cast(int, existing[0])
        row = connection.execute(
            """
            SELECT COALESCE(MAX(event_version), 0)
            FROM industry_chain_events
            WHERE event_id = ?
            """,
            (validated.event_id,),
        ).fetchone()
        event_version = cast(int, row[0]) + 1
        connection.execute(
            """
            INSERT INTO industry_chain_events (
                event_id, event_version, as_of_time, event_time,
                evidence_refs_json, event_json, content_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validated.event_id,
                event_version,
                validated.as_of_time.isoformat(),
                cast(str, validated.event["event_time"]),
                json.dumps(validated.evidence_refs, separators=(",", ":")),
                validated.event_json,
                validated.event_sha256,
                validated.completed_at.isoformat(),
            ),
        )
        return event_version

    def _require_eligible_evidence(
        self,
        connection: sqlite3.Connection,
        *,
        evidence_refs: tuple[str, ...],
        event_id: str,
        as_of_time: datetime,
        require_formal: bool,
    ) -> None:
        placeholders = ",".join("?" for _ in evidence_refs)
        rows = connection.execute(
            f"""
            SELECT evidence.evidence_id, evidence.event_id,
                   evidence.published_at, evidence.updated_at,
                   evidence.source_tier, evidence.access_class,
                   event_link.event_id
            FROM industry_chain_evidence AS evidence
            LEFT JOIN industry_chain_evidence_event_links AS event_link
              ON event_link.evidence_id = evidence.evidence_id
             AND event_link.event_id = ?
            WHERE evidence.evidence_id IN ({placeholders})
            """,
            (event_id, *evidence_refs),
        ).fetchall()
        found = {cast(str, row[0]) for row in rows}
        missing = sorted(set(evidence_refs) - found)
        if missing:
            raise MissingEvidenceError(f"缺少证据：{missing}")
        for row in rows:
            if row[6] is None:
                raise IneligibleEvidenceError("证据不属于当次供需事件")
            published_at = datetime.fromisoformat(cast(str, row[2]))
            updated_at = (
                datetime.fromisoformat(cast(str, row[3]))
                if row[3] is not None
                else None
            )
            if published_at > as_of_time or (
                updated_at is not None and updated_at > as_of_time
            ):
                raise FutureEvidenceError("候选快照引用了未来证据")
            if cast(str, row[5]) == "inaccessible":
                raise IneligibleEvidenceError("不可访问来源不能进入选取快照")
            if require_formal and cast(int, row[4]) > 3:
                raise IneligibleEvidenceError("低等级或不可访问来源不能支撑入选")

    def _connect(self) -> sqlite3.Connection:
        connection = open_database_connection(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _link_evidence_to_event(
    connection: sqlite3.Connection,
    *,
    evidence_id: str,
    event_id: str,
    linked_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO industry_chain_evidence_event_links (
            evidence_id, event_id, linked_at
        ) VALUES (?, ?, ?)
        """,
        (evidence_id, event_id, linked_at.isoformat()),
    )


def read_recent_selections(
    path: Path,
    *,
    limit: int = 10,
) -> tuple[dict[str, object], ...]:
    if not 1 <= limit <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT selection.snapshot_json
            FROM industry_chain_selections AS selection
            WHERE selection.status = 'selected'
              AND json_extract(
                  selection.snapshot_json, '$.rules_version'
              ) = ?
              AND NOT EXISTS (
                SELECT 1
                FROM industry_chain_selection_dismissals AS dismissal
                WHERE dismissal.selection_id = selection.selection_id
                  AND dismissal.selection_version =
                      selection.selection_version
            )
            ORDER BY selection.published_at DESC,
                     selection.selection_id,
                     selection.selection_version DESC
            LIMIT ?
            """,
            (ACTIVE_SELECTION_RULES_VERSION, limit),
        ).fetchall()
    return tuple(
        cast(dict[str, object], json.loads(cast(str, row[0])))
        for row in rows
    )
