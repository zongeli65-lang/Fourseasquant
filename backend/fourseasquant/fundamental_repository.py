from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

from fourseasquant.discussion_sentiment import (
    CombinedDiscussionSignal,
    DiscussionPlatform,
    DiscussionPost,
    PlatformDiscussionAggregate,
    classify_sentiment,
)
from fourseasquant.fundamental_discovery import BoardCandidateSnapshot
from fourseasquant.fundamental_mechanical import (
    PersonalFundamentalMonthlySnapshot,
)


@dataclass(frozen=True)
class EvidenceSaveResult:
    inserted: bool


@dataclass(frozen=True)
class PersonalFundamentalMonthlySaveResult:
    stored: bool
    changed_fields: dict[str, object]


@dataclass(frozen=True)
class BoardCandidateSnapshotSaveResult:
    inserted: bool


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


def save_personal_fundamental_monthly_snapshot(
    path: Path,
    snapshot: PersonalFundamentalMonthlySnapshot,
    *,
    source_urls: list[str],
    created_at: datetime,
) -> PersonalFundamentalMonthlySaveResult:
    existing = read_latest_personal_fundamental_monthly_snapshot(
        path,
        snapshot.code,
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
    if not changed_fields:
        return PersonalFundamentalMonthlySaveResult(
            stored=False,
            changed_fields={},
        )
    with sqlite3.connect(path) as connection:
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
                    changed_fields,
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
    return PersonalFundamentalMonthlySaveResult(
        stored=True,
        changed_fields=changed_fields,
    )


def read_latest_personal_fundamental_monthly_snapshot(
    path: Path,
    code: str,
) -> PersonalFundamentalMonthlySnapshot | None:
    with sqlite3.connect(path) as connection:
        latest = connection.execute(
            """
            SELECT rules_version
            FROM personal_fundamental_monthly_snapshots
            WHERE code = ?
            ORDER BY as_of_date DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone()
    if latest is None:
        return None
    return _read_latest_personal_fundamental_monthly_snapshot_before(
        path,
        code,
        cast(str, latest[0]),
        None,
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
) -> BoardCandidateSnapshot | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM fundamental_board_candidate_snapshots
            WHERE source = ?
            ORDER BY effective_date DESC, collected_at DESC
            LIMIT 1
            """,
            (source,),
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
