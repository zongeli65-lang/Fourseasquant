from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast


@dataclass(frozen=True)
class DiscoveryItem:
    discovery_id: str
    source_id: str
    external_id: str
    security_code: str | None
    security_name: str | None
    headline: str
    published_at: datetime
    collected_at: datetime
    source_url: str
    attachment_url: str | None
    payload: dict[str, object]

    @property
    def payload_json(self) -> str:
        return json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.payload_json.encode()).hexdigest()


@dataclass(frozen=True)
class AppendDiscoveryResult:
    inserted_ids: tuple[str, ...]
    duplicate_count: int


def append_discovery_items(
    path: Path,
    items: tuple[DiscoveryItem, ...],
) -> AppendDiscoveryResult:
    inserted: list[str] = []
    duplicates = 0
    with sqlite3.connect(path) as connection:
        with connection:
            for item in items:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO industry_chain_discovery_items (
                        discovery_id, source_id, external_id, security_code,
                        security_name, headline, published_at, collected_at,
                        source_url, attachment_url, content_sha256, payload_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.discovery_id,
                        item.source_id,
                        item.external_id,
                        item.security_code,
                        item.security_name,
                        item.headline,
                        item.published_at.isoformat(),
                        item.collected_at.isoformat(),
                        item.source_url,
                        item.attachment_url,
                        item.content_sha256,
                        item.payload_json,
                        item.collected_at.isoformat(),
                    ),
                )
                if cursor.rowcount == 1:
                    inserted.append(item.discovery_id)
                else:
                    duplicates += 1
    return AppendDiscoveryResult(tuple(inserted), duplicates)


def read_discovery_items(
    path: Path,
    discovery_ids: tuple[str, ...],
) -> tuple[DiscoveryItem, ...]:
    if not discovery_ids:
        return ()
    placeholders = ",".join("?" for _ in discovery_ids)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"""
            SELECT discovery_id, source_id, external_id, security_code,
                   security_name, headline, published_at, collected_at,
                   source_url, attachment_url, payload_json
            FROM industry_chain_discovery_items
            WHERE discovery_id IN ({placeholders})
            """,
            discovery_ids,
        ).fetchall()
    by_id = {
        cast(str, row[0]): DiscoveryItem(
            discovery_id=cast(str, row[0]),
            source_id=cast(str, row[1]),
            external_id=cast(str, row[2]),
            security_code=cast(str | None, row[3]),
            security_name=cast(str | None, row[4]),
            headline=cast(str, row[5]),
            published_at=datetime.fromisoformat(cast(str, row[6])),
            collected_at=datetime.fromisoformat(cast(str, row[7])),
            source_url=cast(str, row[8]),
            attachment_url=cast(str | None, row[9]),
            payload=cast(dict[str, object], json.loads(cast(str, row[10]))),
        )
        for row in rows
    }
    return tuple(by_id[item_id] for item_id in discovery_ids if item_id in by_id)


def read_source_checkpoint(
    path: Path,
    source_id: str,
) -> str | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT last_external_id
            FROM industry_chain_source_checkpoints
            WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
    return cast(str | None, row[0]) if row is not None else None


def save_source_checkpoint(
    path: Path,
    *,
    source_id: str,
    external_id: str | None,
    published_at: datetime | None,
    checked_at: datetime,
    status: str,
    error_summary: str | None = None,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_source_checkpoints (
                source_id, last_external_id, last_published_at,
                last_checked_at, last_status, error_summary
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                last_external_id = excluded.last_external_id,
                last_published_at = excluded.last_published_at,
                last_checked_at = excluded.last_checked_at,
                last_status = excluded.last_status,
                error_summary = excluded.error_summary
            """,
            (
                source_id,
                external_id,
                published_at.isoformat() if published_at is not None else None,
                checked_at.isoformat(),
                status,
                error_summary,
            ),
        )
