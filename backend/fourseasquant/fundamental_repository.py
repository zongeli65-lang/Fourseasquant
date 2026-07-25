from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from fourseasquant.discussion_sentiment import (
    CombinedDiscussionSignal,
    DiscussionPlatform,
    DiscussionPost,
    PlatformDiscussionAggregate,
    classify_sentiment,
)
from fourseasquant.fundamental_discovery import BoardCandidateSnapshot
from fourseasquant.fundamental_capital_actions import (
    CapitalActionBatchRun,
    CapitalActionEvent,
    CapitalActionSnapshot,
    CapitalActionSnapshotSaveResult,
    PublishedCapitalActionSnapshot,
)
from fourseasquant.fundamental_mechanical import (
    PersonalFundamentalMonthlySnapshot,
    RULES_VERSION,
)


@dataclass(frozen=True)
class EvidenceSaveResult:
    inserted: bool


@dataclass(frozen=True)
class PersonalFundamentalMonthlySaveResult:
    stored: bool
    changed_fields: dict[str, object]


@dataclass(frozen=True)
class PersonalFundamentalMonthlyBatchSaveResult:
    results: dict[str, PersonalFundamentalMonthlySaveResult]
    batch_id: int | None
    published: bool

    @property
    def stored_count(self) -> int:
        return sum(result.stored for result in self.results.values())


@dataclass(frozen=True)
class BoardCandidateSnapshotSaveResult:
    inserted: bool


@dataclass(frozen=True)
class FundamentalUpdateAttempt:
    target_date: date
    stage: str
    status: Literal["succeeded", "failed"]
    attempted_at: datetime
    error_summary: str | None


def create_fundamental_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_parsed_evidence (
            source_url TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            parsed_payload_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (source_url, content_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_fundamental_monthly_snapshots (
            code TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL,
            source_urls_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (code, as_of_date, rules_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_personal_fundamental_monthly_latest
        ON personal_fundamental_monthly_snapshots (
            code, rules_version, as_of_date DESC
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_personal_fundamental_monthly_date_code
        ON personal_fundamental_monthly_snapshots (as_of_date DESC, code)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_fundamental_monthly_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            expected_codes_json TEXT NOT NULL,
            completed_codes_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('published', 'failed')),
            created_at TEXT NOT NULL,
            published_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_personal_fundamental_monthly_batches_date
        ON personal_fundamental_monthly_batches (as_of_date DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discussion_daily_aggregates (
            platform TEXT NOT NULL,
            actual_date TEXT NOT NULL,
            code TEXT NOT NULL,
            post_count INTEGER NOT NULL,
            positive_count INTEGER NOT NULL,
            neutral_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            raw_heat REAL NOT NULL,
            weighted_sentiment REAL NOT NULL,
            heat_percentile REAL,
            likes_missing INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (platform, actual_date, code)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discussion_post_references (
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            actual_date TEXT NOT NULL,
            code TEXT NOT NULL,
            url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            likes INTEGER,
            sentiment TEXT NOT NULL,
            PRIMARY KEY (platform, post_id, code)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discussion_daily_combined_signals (
            actual_date TEXT NOT NULL,
            code TEXT NOT NULL,
            heat_percentile REAL NOT NULL,
            weighted_sentiment REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, code)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discussion_daily_code_date
        ON discussion_daily_aggregates (code, actual_date DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discussion_combined_code_date
        ON discussion_daily_combined_signals (code, actual_date DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discussion_post_reference_date
        ON discussion_post_references (actual_date)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_board_candidate_snapshots (
            source TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY (source, effective_date, content_sha256)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS capital_action_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date TEXT NOT NULL,
            insider_window_complete INTEGER NOT NULL DEFAULT 1,
            expected_codes_json TEXT NOT NULL,
            completed_codes_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('published', 'failed')),
            errors_json TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            published_at TEXT
        )
        """
    )
    capital_batch_columns = {
        cast(str, row[1])
        for row in connection.execute(
            "PRAGMA table_info(capital_action_batches)"
        )
    }
    if "insider_window_complete" not in capital_batch_columns:
        connection.execute(
            """
            ALTER TABLE capital_action_batches
            ADD COLUMN insider_window_complete INTEGER NOT NULL DEFAULT 0
            """
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capital_action_batches_date
        ON capital_action_batches (as_of_date DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS capital_action_events (
            batch_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            code TEXT NOT NULL,
            event_type TEXT NOT NULL,
            announcement_at TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            amount_cny REAL,
            shares REAL,
            shares_before REAL,
            confirmed_for_score INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            event_json TEXT NOT NULL,
            PRIMARY KEY (batch_id, event_key),
            FOREIGN KEY (batch_id) REFERENCES capital_action_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capital_action_events_code_date
        ON capital_action_events (code, effective_date DESC, batch_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS capital_action_publications (
            as_of_date TEXT PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            FOREIGN KEY (batch_id) REFERENCES capital_action_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_update_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
            attempted_at TEXT NOT NULL,
            error_summary TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fundamental_update_attempts_date
        ON fundamental_update_attempts (target_date DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamental_update_claims (
            target_date TEXT PRIMARY KEY,
            claimed_at TEXT NOT NULL,
            claim_id TEXT NOT NULL
        )
        """
    )


def claim_fundamental_update(
    path: Path,
    target_date: date,
    claimed_at: datetime,
    *,
    stale_after_seconds: int = 7_200,
) -> str | None:
    stale_before = claimed_at.timestamp() - stale_after_seconds
    claim_id = str(uuid.uuid4())
    with sqlite3.connect(path) as connection:
        existing = connection.execute(
            """
            SELECT claimed_at, claim_id
            FROM fundamental_update_claims
            WHERE target_date = ?
            """,
            (target_date.isoformat(),),
        ).fetchone()
        if existing is not None:
            existing_time = datetime.fromisoformat(cast(str, existing[0]))
            if existing_time.timestamp() < stale_before:
                connection.execute(
                    """
                    DELETE FROM fundamental_update_claims
                    WHERE target_date = ? AND claim_id = ?
                    """,
                    (target_date.isoformat(), cast(str, existing[1])),
                )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO fundamental_update_claims (
                target_date, claimed_at, claim_id
            ) VALUES (?, ?, ?)
            """,
            (target_date.isoformat(), claimed_at.isoformat(), claim_id),
        )
    return claim_id if cursor.rowcount == 1 else None


def release_fundamental_update(
    path: Path,
    target_date: date,
    claim_id: str,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            DELETE FROM fundamental_update_claims
            WHERE target_date = ? AND claim_id = ?
            """,
            (target_date.isoformat(), claim_id),
        )


def renew_fundamental_update(
    path: Path,
    target_date: date,
    claim_id: str,
    renewed_at: datetime,
) -> bool:
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            UPDATE fundamental_update_claims
            SET claimed_at = ?
            WHERE target_date = ? AND claim_id = ?
            """,
            (
                renewed_at.isoformat(),
                target_date.isoformat(),
                claim_id,
            ),
        )
    return cursor.rowcount == 1


def save_parsed_evidence(
    path: Path,
    *,
    source_url: str,
    content_sha256: str,
    parsed_payload: dict[str, object],
    first_seen_at: datetime,
) -> EvidenceSaveResult:
    payload_json = json.dumps(
        parsed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO fundamental_parsed_evidence (
                source_url,
                content_sha256,
                parsed_payload_json,
                first_seen_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                source_url,
                content_sha256,
                payload_json,
                first_seen_at.isoformat(),
            ),
        )
    return EvidenceSaveResult(inserted=cursor.rowcount == 1)


def save_capital_action_snapshot(
    path: Path,
    snapshot: CapitalActionSnapshot,
    *,
    collected_at: datetime,
) -> CapitalActionSnapshotSaveResult:
    status: Literal["published", "failed"] = (
        "published" if snapshot.complete else "failed"
    )
    published_at = collected_at if snapshot.complete else None
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO capital_action_batches (
                    as_of_date,
                    insider_window_complete,
                    expected_codes_json,
                    completed_codes_json,
                    status,
                    errors_json,
                    collected_at,
                    published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.as_of_date.isoformat(),
                    int(snapshot.insider_window_complete),
                    json.dumps(snapshot.expected_codes, separators=(",", ":")),
                    json.dumps(snapshot.completed_codes, separators=(",", ":")),
                    status,
                    json.dumps(
                        snapshot.errors,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    collected_at.isoformat(),
                    published_at.isoformat() if published_at else None,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("资本行为批次未生成主键")
            batch_id = cursor.lastrowid
            for event in snapshot.events:
                event_json = event.model_dump_json()
                connection.execute(
                    """
                    INSERT INTO capital_action_events (
                        batch_id,
                        event_key,
                        code,
                        event_type,
                        announcement_at,
                        effective_date,
                        amount_cny,
                        shares,
                        shares_before,
                        confirmed_for_score,
                        source_name,
                        source_url,
                        content_sha256,
                        event_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        event.event_key,
                        event.code,
                        event.event_type.value,
                        event.announcement_at.isoformat(),
                        event.effective_date.isoformat(),
                        event.amount_cny,
                        event.shares,
                        event.shares_before,
                        int(event.confirmed_for_score),
                        event.source_name,
                        event.source_url,
                        event.content_sha256,
                        event_json,
                    ),
                )
            if snapshot.complete:
                connection.execute(
                    """
                    INSERT INTO capital_action_publications (
                        as_of_date,
                        batch_id,
                        published_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(as_of_date) DO UPDATE SET
                        batch_id = excluded.batch_id,
                        published_at = excluded.published_at
                    """,
                    (
                        snapshot.as_of_date.isoformat(),
                        batch_id,
                        collected_at.isoformat(),
                    ),
                )
    return CapitalActionSnapshotSaveResult(
        batch_id=batch_id,
        status=status,
        published=snapshot.complete,
    )


def read_latest_published_capital_action_snapshot(
    path: Path,
    *,
    as_of_date: date | None = None,
) -> PublishedCapitalActionSnapshot | None:
    parameters: tuple[object, ...] = ()
    date_clause = ""
    if as_of_date is not None:
        date_clause = "WHERE publication.as_of_date <= ?"
        parameters = (as_of_date.isoformat(),)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"""
            SELECT
                batch.id,
                batch.as_of_date,
                batch.insider_window_complete,
                batch.expected_codes_json,
                batch.completed_codes_json,
                batch.errors_json,
                publication.published_at
            FROM capital_action_publications AS publication
            JOIN capital_action_batches AS batch
              ON batch.id = publication.batch_id
            {date_clause}
            ORDER BY publication.as_of_date DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        event_rows = connection.execute(
            """
            SELECT event_json
            FROM capital_action_events
            WHERE batch_id = ?
            ORDER BY announcement_at, effective_date, event_type, event_key
            """,
            (row[0],),
        ).fetchall()
    return PublishedCapitalActionSnapshot(
        batch_id=int(row[0]),
        as_of_date=date.fromisoformat(cast(str, row[1])),
        insider_window_complete=bool(row[2]),
        expected_codes=json.loads(cast(str, row[3])),
        completed_codes=json.loads(cast(str, row[4])),
        errors=json.loads(cast(str, row[5])),
        events=[
            CapitalActionEvent.model_validate_json(cast(str, item[0]))
            for item in event_rows
        ],
        published_at=datetime.fromisoformat(cast(str, row[6])),
    )


def read_latest_capital_action_batch(
    path: Path,
    *,
    as_of_date: date | None = None,
) -> CapitalActionBatchRun | None:
    parameters: tuple[object, ...] = ()
    date_clause = ""
    if as_of_date is not None:
        date_clause = "WHERE as_of_date <= ?"
        parameters = (as_of_date.isoformat(),)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"""
            SELECT
                id,
                as_of_date,
                insider_window_complete,
                expected_codes_json,
                completed_codes_json,
                status,
                errors_json,
                collected_at,
                published_at
            FROM capital_action_batches
            {date_clause}
            ORDER BY as_of_date DESC, id DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
        if row is None:
            return None
        event_rows = connection.execute(
            """
            SELECT event_json
            FROM capital_action_events
            WHERE batch_id = ?
            ORDER BY announcement_at, effective_date, event_type, event_key
            """,
            (row[0],),
        ).fetchall()
    return CapitalActionBatchRun(
        batch_id=int(row[0]),
        as_of_date=date.fromisoformat(cast(str, row[1])),
        insider_window_complete=bool(row[2]),
        expected_codes=json.loads(cast(str, row[3])),
        completed_codes=json.loads(cast(str, row[4])),
        status=cast(Literal["published", "failed"], row[5]),
        errors=json.loads(cast(str, row[6])),
        events=[
            CapitalActionEvent.model_validate_json(cast(str, item[0]))
            for item in event_rows
        ],
        collected_at=datetime.fromisoformat(cast(str, row[7])),
        published_at=(
            datetime.fromisoformat(cast(str, row[8]))
            if row[8] is not None
            else None
        ),
    )


def save_fundamental_update_attempt(
    path: Path,
    attempt: FundamentalUpdateAttempt,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO fundamental_update_attempts (
                target_date,
                stage,
                status,
                attempted_at,
                error_summary
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                attempt.target_date.isoformat(),
                attempt.stage,
                attempt.status,
                attempt.attempted_at.isoformat(),
                attempt.error_summary,
            ),
        )


def read_latest_fundamental_update_attempt(
    path: Path,
    *,
    target_date: date,
) -> FundamentalUpdateAttempt | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT target_date, stage, status, attempted_at, error_summary
            FROM fundamental_update_attempts
            WHERE target_date <= ?
            ORDER BY target_date DESC, id DESC
            LIMIT 1
            """,
            (target_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return FundamentalUpdateAttempt(
        target_date=date.fromisoformat(cast(str, row[0])),
        stage=cast(str, row[1]),
        status=cast(Literal["succeeded", "failed"], row[2]),
        attempted_at=datetime.fromisoformat(cast(str, row[3])),
        error_summary=cast(str | None, row[4]),
    )


def read_monthly_snapshot_coverage(
    path: Path,
    *,
    as_of_date: date,
    codes: list[str],
    rules_version: str = RULES_VERSION,
) -> set[str]:
    if not codes:
        return set()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT completed_codes_json
            FROM personal_fundamental_monthly_batches
            WHERE as_of_date = ? AND rules_version = ?
              AND status = 'published'
            ORDER BY id DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(), rules_version),
        ).fetchone()
    if row is None:
        return set()
    requested = set(codes)
    completed = set(json.loads(cast(str, row[0])))
    return requested.intersection(completed)


def save_personal_fundamental_monthly_snapshot(
    path: Path,
    snapshot: PersonalFundamentalMonthlySnapshot,
    *,
    source_urls: list[str],
    created_at: datetime,
) -> PersonalFundamentalMonthlySaveResult:
    batch = save_personal_fundamental_monthly_batch(
        path,
        [(snapshot, source_urls)],
        created_at=created_at,
        publish=False,
    )
    return batch.results[snapshot.code]


def save_personal_fundamental_monthly_batch(
    path: Path,
    snapshots: list[
        tuple[PersonalFundamentalMonthlySnapshot, list[str]]
    ],
    *,
    created_at: datetime,
    publish: bool = True,
) -> PersonalFundamentalMonthlyBatchSaveResult:
    if not snapshots:
        raise ValueError("月度批次不能为空")
    codes = [snapshot.code for snapshot, _ in snapshots]
    if len(codes) != len(set(codes)):
        raise ValueError("月度批次不能包含重复股票")
    as_of_dates = {snapshot.as_of_date for snapshot, _ in snapshots}
    rules_versions = {snapshot.rules_version for snapshot, _ in snapshots}
    if len(as_of_dates) != 1 or len(rules_versions) != 1:
        raise ValueError("月度批次必须使用同一日期和规则版本")
    prepared: list[
        tuple[
            PersonalFundamentalMonthlySnapshot,
            list[str],
            PersonalFundamentalMonthlySaveResult,
        ]
    ] = []
    for snapshot, source_urls in snapshots:
        result = _prepare_personal_fundamental_monthly_save(path, snapshot)
        prepared.append((snapshot, source_urls, result))
    with sqlite3.connect(path) as connection:
        with connection:
            for snapshot, source_urls, result in prepared:
                if not result.stored:
                    continue
                connection.execute(
                    """
                    INSERT INTO personal_fundamental_monthly_snapshots (
                        code,
                        as_of_date,
                        rules_version,
                        changed_fields_json,
                        source_urls_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code, as_of_date, rules_version) DO UPDATE SET
                        changed_fields_json = excluded.changed_fields_json,
                        source_urls_json = excluded.source_urls_json,
                        created_at = excluded.created_at
                    """,
                    (
                        snapshot.code,
                        snapshot.as_of_date.isoformat(),
                        snapshot.rules_version,
                        json.dumps(
                            result.changed_fields,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            sorted(set(source_urls)),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        created_at.isoformat(),
                    ),
                )
            batch_id: int | None = None
            if publish:
                cursor = connection.execute(
                    """
                    INSERT INTO personal_fundamental_monthly_batches (
                        as_of_date,
                        rules_version,
                        expected_codes_json,
                        completed_codes_json,
                        status,
                        created_at,
                        published_at
                    ) VALUES (?, ?, ?, ?, 'published', ?, ?)
                    """,
                    (
                        snapshots[0][0].as_of_date.isoformat(),
                        snapshots[0][0].rules_version,
                        json.dumps(sorted(codes), separators=(",", ":")),
                        json.dumps(sorted(codes), separators=(",", ":")),
                        created_at.isoformat(),
                        created_at.isoformat(),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("月度基本面批次未生成主键")
                batch_id = int(cursor.lastrowid)
    return PersonalFundamentalMonthlyBatchSaveResult(
        results={
            snapshot.code: result
            for snapshot, _, result in prepared
        },
        batch_id=batch_id,
        published=publish,
    )


def read_latest_personal_fundamental_monthly_snapshot(
    path: Path,
    code: str,
    *,
    rules_version: str = RULES_VERSION,
) -> PersonalFundamentalMonthlySnapshot | None:
    return _read_latest_personal_fundamental_monthly_snapshot_before(
        path,
        code,
        rules_version,
        None,
    )


def _prepare_personal_fundamental_monthly_save(
    path: Path,
    snapshot: PersonalFundamentalMonthlySnapshot,
) -> PersonalFundamentalMonthlySaveResult:
    existing = read_latest_personal_fundamental_monthly_snapshot(
        path,
        snapshot.code,
        rules_version=snapshot.rules_version,
    )
    if existing == snapshot:
        return PersonalFundamentalMonthlySaveResult(
            stored=False,
            changed_fields={},
        )
    previous = _read_latest_personal_fundamental_monthly_snapshot_before(
        path,
        snapshot.code,
        snapshot.rules_version,
        snapshot.as_of_date.isoformat(),
    )
    current_values = _analysis_values(snapshot)
    previous_values = _analysis_values(previous) if previous else {}
    changed_fields = {
        key: value
        for key, value in current_values.items()
        if key not in previous_values or previous_values[key] != value
    }
    return PersonalFundamentalMonthlySaveResult(
        stored=bool(changed_fields),
        changed_fields=changed_fields,
    )


def save_discussion_day(
    path: Path,
    *,
    posts: list[DiscussionPost],
    aggregate: PlatformDiscussionAggregate,
    created_at: datetime,
) -> None:
    cutoff = aggregate.actual_date - timedelta(days=30)
    with sqlite3.connect(path) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO discussion_daily_aggregates (
                    platform,
                    actual_date,
                    code,
                    post_count,
                    positive_count,
                    neutral_count,
                    negative_count,
                    raw_heat,
                    weighted_sentiment,
                    heat_percentile,
                    likes_missing,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, actual_date, code) DO UPDATE SET
                    post_count = excluded.post_count,
                    positive_count = excluded.positive_count,
                    neutral_count = excluded.neutral_count,
                    negative_count = excluded.negative_count,
                    raw_heat = excluded.raw_heat,
                    weighted_sentiment = excluded.weighted_sentiment,
                    heat_percentile = excluded.heat_percentile,
                    likes_missing = excluded.likes_missing,
                    created_at = excluded.created_at
                """,
                (
                    aggregate.platform,
                    aggregate.actual_date.isoformat(),
                    aggregate.code,
                    aggregate.post_count,
                    aggregate.positive_count,
                    aggregate.neutral_count,
                    aggregate.negative_count,
                    aggregate.raw_heat,
                    aggregate.weighted_sentiment,
                    aggregate.heat_percentile,
                    int(aggregate.likes_missing),
                    created_at.isoformat(),
                ),
            )
            for post in posts:
                if (
                    post.platform != aggregate.platform
                    or post.code != aggregate.code
                    or post.published_at.date() != aggregate.actual_date
                ):
                    raise ValueError("帖子与每日平台汇总不一致")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO discussion_post_references (
                        platform,
                        post_id,
                        actual_date,
                        code,
                        url,
                        published_at,
                        likes,
                        sentiment
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post.platform,
                        post.post_id,
                        post.published_at.date().isoformat(),
                        post.code,
                        post.url,
                        post.published_at.isoformat(),
                        post.likes,
                        classify_sentiment(post.text),
                    ),
                )
            connection.execute(
                """
                DELETE FROM discussion_post_references
                WHERE actual_date < ?
                """,
                (cutoff.isoformat(),),
            )


def count_discussion_post_references(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        row = cast(
            tuple[int],
            connection.execute(
                "SELECT COUNT(*) FROM discussion_post_references"
            ).fetchone(),
        )
    return row[0]


def read_platform_discussion_aggregate(
    path: Path,
    *,
    platform: DiscussionPlatform,
    actual_date: date,
    code: str,
) -> PlatformDiscussionAggregate | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT post_count, positive_count, neutral_count, negative_count,
                   raw_heat, weighted_sentiment, heat_percentile, likes_missing
            FROM discussion_daily_aggregates
            WHERE platform = ? AND actual_date = ? AND code = ?
            """,
            (platform, actual_date.isoformat(), code),
        ).fetchone()
    if row is None:
        return None
    return PlatformDiscussionAggregate(
        platform=platform,
        actual_date=actual_date,
        code=code,
        post_count=cast(int, row[0]),
        positive_count=cast(int, row[1]),
        neutral_count=cast(int, row[2]),
        negative_count=cast(int, row[3]),
        raw_heat=cast(float, row[4]),
        weighted_sentiment=cast(float, row[5]),
        heat_percentile=cast(float | None, row[6]),
        likes_missing=bool(row[7]),
    )


def save_combined_discussion_signal(
    path: Path,
    signal: CombinedDiscussionSignal,
    *,
    created_at: datetime,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO discussion_daily_combined_signals (
                actual_date,
                code,
                heat_percentile,
                weighted_sentiment,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(actual_date, code) DO UPDATE SET
                heat_percentile = excluded.heat_percentile,
                weighted_sentiment = excluded.weighted_sentiment,
                created_at = excluded.created_at
            """,
            (
                signal.actual_date.isoformat(),
                signal.code,
                signal.heat_percentile,
                signal.weighted_sentiment,
                created_at.isoformat(),
            ),
        )


def read_combined_discussion_signal(
    path: Path,
    *,
    actual_date: date,
    code: str,
) -> CombinedDiscussionSignal | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT heat_percentile, weighted_sentiment
            FROM discussion_daily_combined_signals
            WHERE actual_date = ? AND code = ?
            """,
            (actual_date.isoformat(), code),
        ).fetchone()
    if row is None:
        return None
    return CombinedDiscussionSignal(
        actual_date=actual_date,
        code=code,
        heat_percentile=cast(float, row[0]),
        weighted_sentiment=cast(float, row[1]),
    )


def save_board_candidate_snapshot(
    path: Path,
    snapshot: BoardCandidateSnapshot,
    *,
    collected_at: datetime,
) -> BoardCandidateSnapshotSaveResult:
    if not snapshot.complete:
        raise ValueError("板块候选快照不完整，禁止写入正式候选库")
    payload_json = snapshot.model_dump_json()
    content_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO fundamental_board_candidate_snapshots (
                source,
                effective_date,
                content_sha256,
                payload_json,
                collected_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.source,
                snapshot.effective_date.isoformat(),
                content_sha256,
                payload_json,
                collected_at.isoformat(),
            ),
        )
    return BoardCandidateSnapshotSaveResult(inserted=cursor.rowcount == 1)


def read_latest_board_candidate_snapshot(
    path: Path,
    source: str,
    *,
    effective_on_or_before: date | None = None,
) -> BoardCandidateSnapshot | None:
    date_clause = (
        "AND effective_date <= ?"
        if effective_on_or_before is not None
        else ""
    )
    parameters: tuple[str, ...] = (
        (source, effective_on_or_before.isoformat())
        if effective_on_or_before is not None
        else (source,)
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            f"""
            SELECT payload_json
            FROM fundamental_board_candidate_snapshots
            WHERE source = ?
            {date_clause}
            ORDER BY effective_date DESC, collected_at DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()
    if row is None:
        return None
    return BoardCandidateSnapshot.model_validate_json(cast(str, row[0]))


def _analysis_values(
    snapshot: PersonalFundamentalMonthlySnapshot,
) -> dict[str, object]:
    payload = snapshot.model_dump(mode="json")
    return {
        key: value
        for key, value in payload.items()
        if key not in {"rules_version", "code", "as_of_date"}
    }


def _read_latest_personal_fundamental_monthly_snapshot_before(
    path: Path,
    code: str,
    rules_version: str,
    before_date: str | None,
) -> PersonalFundamentalMonthlySnapshot | None:
    where_before = "AND as_of_date < ?" if before_date is not None else ""
    parameters: tuple[str, ...] = (
        (code, rules_version, before_date)
        if before_date is not None
        else (code, rules_version)
    )
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT as_of_date, changed_fields_json
            FROM personal_fundamental_monthly_snapshots
            WHERE code = ? AND rules_version = ?
            {where_before}
            ORDER BY as_of_date
            """,
            parameters,
        ).fetchall()
    if not rows:
        return None
    state: dict[str, object] = {
        "rules_version": rules_version,
        "code": code,
    }
    for as_of_date, changed_fields_json in rows:
        state["as_of_date"] = cast(str, as_of_date)
        changed = cast(dict[str, object], json.loads(changed_fields_json))
        state.update(changed)
    return PersonalFundamentalMonthlySnapshot.model_validate(state)
