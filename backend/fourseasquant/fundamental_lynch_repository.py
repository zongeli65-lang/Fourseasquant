from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast

from fourseasquant.fundamental_lynch import (
    LYNCH_RULES_VERSION,
    LynchDailyResult,
    LynchFinancialBase,
)


@dataclass(frozen=True)
class LynchBatchSaveResult:
    batch_id: int
    published: bool
    expected_count: int
    completed_count: int


@dataclass(frozen=True)
class PublishedLynchFinancialBatch:
    batch_id: int
    target_date: date
    rules_version: str
    expected_codes: list[str]
    bases: dict[str, LynchFinancialBase]
    published_at: datetime


@dataclass(frozen=True)
class PublishedLynchDailyBatch:
    batch_id: int
    target_date: date
    financial_base_date: date
    rules_version: str
    expected_codes: list[str]
    results: dict[str, LynchDailyResult]
    published_at: datetime


def create_lynch_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_financial_base_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            expected_codes_json TEXT NOT NULL,
            completed_codes_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('published', 'failed')),
            errors_json TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            published_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lynch_financial_batches_date
        ON lynch_financial_base_batches (
            rules_version, target_date DESC, id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_financial_bases (
            batch_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (batch_id, code),
            FOREIGN KEY (batch_id)
                REFERENCES lynch_financial_base_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_financial_base_publications (
            target_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            batch_id INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (target_date, rules_version),
            FOREIGN KEY (batch_id)
                REFERENCES lynch_financial_base_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_daily_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            financial_base_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            expected_codes_json TEXT NOT NULL,
            completed_codes_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('published', 'failed')),
            errors_json TEXT NOT NULL,
            calculated_at TEXT NOT NULL,
            published_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lynch_daily_batches_date
        ON lynch_daily_batches (rules_version, target_date DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_daily_results (
            batch_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            lynch_ratio REAL,
            market_percentile REAL,
            ranking_eligible INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (batch_id, code),
            FOREIGN KEY (batch_id) REFERENCES lynch_daily_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_lynch_daily_rank
        ON lynch_daily_results (
            batch_id, ranking_eligible, market_percentile DESC, code
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_daily_publications (
            target_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            batch_id INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (target_date, rules_version),
            FOREIGN KEY (batch_id) REFERENCES lynch_daily_batches(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS lynch_financial_collection_cache (
            target_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            code TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY (target_date, rules_version, code)
        )
        """
    )


def save_lynch_financial_base_batch(
    path: Path,
    *,
    target_date: date,
    expected_codes: list[str],
    bases: list[LynchFinancialBase],
    errors: dict[str, str],
    collected_at: datetime,
    rules_version: str = LYNCH_RULES_VERSION,
) -> LynchBatchSaveResult:
    expected = sorted(set(expected_codes))
    base_by_code = {item.code: item for item in bases}
    completed = sorted(base_by_code)
    complete = not errors and completed == expected
    status: Literal["published", "failed"] = (
        "published" if complete else "failed"
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO lynch_financial_base_batches (
                    target_date, rules_version, expected_codes_json,
                    completed_codes_json, status, errors_json,
                    collected_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_date.isoformat(),
                    rules_version,
                    _json(expected),
                    _json(completed),
                    status,
                    _json(errors),
                    collected_at.isoformat(),
                    collected_at.isoformat() if complete else None,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("全市场林奇财务批次未生成主键")
            batch_id = cursor.lastrowid
            if complete:
                connection.executemany(
                    """
                    INSERT INTO lynch_financial_bases (
                        batch_id, code, payload_json
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (batch_id, code, base_by_code[code].model_dump_json())
                        for code in completed
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO lynch_financial_base_publications (
                        target_date, rules_version, batch_id, published_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(target_date, rules_version) DO UPDATE SET
                        batch_id = excluded.batch_id,
                        published_at = excluded.published_at
                    """,
                    (
                        target_date.isoformat(),
                        rules_version,
                        batch_id,
                        collected_at.isoformat(),
                    ),
                )
    return LynchBatchSaveResult(
        batch_id=batch_id,
        published=complete,
        expected_count=len(expected),
        completed_count=len(completed),
    )


def save_lynch_daily_batch(
    path: Path,
    *,
    target_date: date,
    financial_base_date: date,
    expected_codes: list[str],
    results: list[LynchDailyResult],
    errors: dict[str, str],
    calculated_at: datetime,
    rules_version: str = LYNCH_RULES_VERSION,
) -> LynchBatchSaveResult:
    expected = sorted(set(expected_codes))
    result_by_code = {item.code: item for item in results}
    completed = sorted(result_by_code)
    dates_match = all(item.target_date == target_date for item in results)
    complete = (
        not errors
        and completed == expected
        and dates_match
        and len(result_by_code) == len(results)
    )
    status: Literal["published", "failed"] = (
        "published" if complete else "failed"
    )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO lynch_daily_batches (
                    target_date, financial_base_date, rules_version,
                    expected_codes_json, completed_codes_json, status,
                    errors_json, calculated_at, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_date.isoformat(),
                    financial_base_date.isoformat(),
                    rules_version,
                    _json(expected),
                    _json(completed),
                    status,
                    _json(errors),
                    calculated_at.isoformat(),
                    calculated_at.isoformat() if complete else None,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("全市场林奇日频批次未生成主键")
            batch_id = cursor.lastrowid
            if complete:
                connection.executemany(
                    """
                    INSERT INTO lynch_daily_results (
                        batch_id, code, lynch_ratio, market_percentile,
                        ranking_eligible, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch_id,
                            code,
                            result_by_code[code].lynch_ratio,
                            result_by_code[code].market_percentile,
                            int(result_by_code[code].ranking_eligible),
                            result_by_code[code].model_dump_json(),
                        )
                        for code in completed
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO lynch_daily_publications (
                        target_date, rules_version, batch_id, published_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(target_date, rules_version) DO UPDATE SET
                        batch_id = excluded.batch_id,
                        published_at = excluded.published_at
                    """,
                    (
                        target_date.isoformat(),
                        rules_version,
                        batch_id,
                        calculated_at.isoformat(),
                    ),
                )
    return LynchBatchSaveResult(
        batch_id=batch_id,
        published=complete,
        expected_count=len(expected),
        completed_count=len(completed),
    )


def read_latest_published_lynch_financial_batch(
    path: Path,
    target_date: date,
    *,
    rules_version: str = LYNCH_RULES_VERSION,
) -> PublishedLynchFinancialBatch | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT batch.id, batch.target_date, batch.rules_version,
                   batch.expected_codes_json, publication.published_at
            FROM lynch_financial_base_publications AS publication
            JOIN lynch_financial_base_batches AS batch
              ON batch.id = publication.batch_id
            WHERE publication.rules_version = ?
              AND publication.target_date <= ?
            ORDER BY publication.target_date DESC, publication.batch_id DESC
            LIMIT 1
            """,
            (rules_version, target_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        payload_rows = connection.execute(
            """
            SELECT code, payload_json
            FROM lynch_financial_bases
            WHERE batch_id = ?
            ORDER BY code
            """,
            (cast(int, row[0]),),
        ).fetchall()
    return PublishedLynchFinancialBatch(
        batch_id=cast(int, row[0]),
        target_date=date.fromisoformat(cast(str, row[1])),
        rules_version=cast(str, row[2]),
        expected_codes=cast(list[str], json.loads(cast(str, row[3]))),
        bases={
            cast(str, payload[0]): LynchFinancialBase.model_validate_json(
                cast(str, payload[1])
            )
            for payload in payload_rows
        },
        published_at=datetime.fromisoformat(cast(str, row[4])),
    )


def read_latest_published_lynch_daily_batch(
    path: Path,
    target_date: date,
    *,
    rules_version: str = LYNCH_RULES_VERSION,
) -> PublishedLynchDailyBatch | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT batch.id, batch.target_date, batch.financial_base_date,
                   batch.rules_version, batch.expected_codes_json,
                   publication.published_at
            FROM lynch_daily_publications AS publication
            JOIN lynch_daily_batches AS batch
              ON batch.id = publication.batch_id
            WHERE publication.rules_version = ?
              AND publication.target_date <= ?
            ORDER BY publication.target_date DESC, publication.batch_id DESC
            LIMIT 1
            """,
            (rules_version, target_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        payload_rows = connection.execute(
            """
            SELECT code, payload_json
            FROM lynch_daily_results
            WHERE batch_id = ?
            ORDER BY code
            """,
            (cast(int, row[0]),),
        ).fetchall()
    return PublishedLynchDailyBatch(
        batch_id=cast(int, row[0]),
        target_date=date.fromisoformat(cast(str, row[1])),
        financial_base_date=date.fromisoformat(cast(str, row[2])),
        rules_version=cast(str, row[3]),
        expected_codes=cast(list[str], json.loads(cast(str, row[4]))),
        results={
            cast(str, payload[0]): LynchDailyResult.model_validate_json(
                cast(str, payload[1])
            )
            for payload in payload_rows
        },
        published_at=datetime.fromisoformat(cast(str, row[5])),
    )


def save_lynch_financial_collection_item(
    path: Path,
    *,
    target_date: date,
    base: LynchFinancialBase,
    collected_at: datetime,
    rules_version: str = LYNCH_RULES_VERSION,
) -> None:
    with sqlite3.connect(path) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO lynch_financial_collection_cache (
                    target_date, rules_version, code, payload_json,
                    collected_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_date, rules_version, code) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    collected_at = excluded.collected_at
                """,
                (
                    target_date.isoformat(),
                    rules_version,
                    base.code,
                    base.model_dump_json(),
                    collected_at.isoformat(),
                ),
            )


def read_lynch_financial_collection_cache(
    path: Path,
    *,
    target_date: date,
    rules_version: str = LYNCH_RULES_VERSION,
) -> dict[str, LynchFinancialBase]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT code, payload_json
            FROM lynch_financial_collection_cache
            WHERE target_date = ? AND rules_version = ?
            ORDER BY code
            """,
            (target_date.isoformat(), rules_version),
        ).fetchall()
    return {
        cast(str, row[0]): LynchFinancialBase.model_validate_json(
            cast(str, row[1])
        )
        for row in rows
    }


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
