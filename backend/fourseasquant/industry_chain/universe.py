from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

ELIGIBILITY_RULES_VERSION = "industry-chain-main-board-v1"
MINIMUM_LISTING_TRADING_DAYS = 60
MAIN_BOARD_CODE = re.compile(r"^(?:000|001|002|003|600|601|603|605)\d{3}$")
HISTORY_SOURCE = "akshare_sina_daily"
BEIJING = ZoneInfo("Asia/Shanghai")


class EligibleUniverseUnavailable(LookupError):
    """指定时间点之前没有已发布的合格股票范围。"""


@dataclass(frozen=True)
class EligibleSecurity:
    code: str
    name: str
    listing_trading_days: int


@dataclass(frozen=True)
class EligibleUniverseSnapshot:
    universe_version: str
    available_as_of_time: datetime
    actual_data_date: date
    source: str
    source_published_at: datetime
    eligibility_rules_version: str
    minimum_listing_trading_days: int
    securities: tuple[EligibleSecurity, ...]

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(item.code for item in self.securities)


class SqliteEligibleUniverseAdapter:
    """从已发布日频事实生成并冻结产业链可用股票范围。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, *, as_of_time: datetime) -> EligibleUniverseSnapshot:
        _require_aware(as_of_time)
        normalized_as_of = as_of_time.astimezone(BEIJING)
        with self._connect() as connection:
            publication = connection.execute(
                """
                SELECT actual_data_date, qfq_source, published_at
                FROM candle_dataset_publications
                WHERE published_at <= ? AND actual_data_date <= ?
                ORDER BY actual_data_date DESC, published_at DESC
                LIMIT 1
                """,
                (
                    normalized_as_of.isoformat(),
                    normalized_as_of.date().isoformat(),
                ),
            ).fetchone()
            if publication is None:
                raise EligibleUniverseUnavailable(
                    "as_of_time 之前没有已发布的日频股票范围"
                )
            actual_data_date = date.fromisoformat(cast(str, publication[0]))
            publication_source = cast(str, publication[1])
            source = f"{HISTORY_SOURCE}|publication={publication_source}"
            source_published_at = datetime.fromisoformat(
                cast(str, publication[2])
            )
            rows = connection.execute(
                """
                SELECT code, name, listing_trading_days
                FROM historical_security_facts
                WHERE source = ? AND actual_data_date = ?
                  AND close > 0
                  AND volume > 0
                  AND turnover_cny > 0
                  AND listing_trading_days >= ?
                ORDER BY code
                """,
                (
                    HISTORY_SOURCE,
                    actual_data_date.isoformat(),
                    MINIMUM_LISTING_TRADING_DAYS,
                ),
            ).fetchall()
            securities = tuple(
                EligibleSecurity(
                    code=cast(str, row[0]),
                    name=cast(str, row[1]),
                    listing_trading_days=cast(int, row[2]),
                )
                for row in rows
                if _is_eligible(cast(str, row[0]), cast(str, row[1]))
            )
            if not securities:
                raise EligibleUniverseUnavailable("没有合格的沪深主板股票")
            snapshot = _build_snapshot(
                actual_data_date=actual_data_date,
                source=source,
                source_published_at=source_published_at,
                securities=securities,
            )
            self._freeze(connection, snapshot)
        return snapshot

    def read(self, universe_version: str) -> EligibleUniverseSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM industry_chain_stock_universes
                WHERE universe_version = ?
                """,
                (universe_version,),
            ).fetchone()
        if row is None:
            return None
        return _snapshot_from_json(cast(str, row[0]))

    def _freeze(
        self,
        connection: sqlite3.Connection,
        snapshot: EligibleUniverseSnapshot,
    ) -> None:
        payload_json = _snapshot_json(snapshot)
        content_sha256 = hashlib.sha256(payload_json.encode()).hexdigest()
        connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_stock_universes (
                universe_version, as_of_time, actual_data_date, source,
                source_published_at, eligibility_rules_version,
                minimum_listing_trading_days, security_count, content_sha256,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.universe_version,
                snapshot.available_as_of_time.isoformat(),
                snapshot.actual_data_date.isoformat(),
                snapshot.source,
                snapshot.source_published_at.isoformat(),
                snapshot.eligibility_rules_version,
                snapshot.minimum_listing_trading_days,
                len(snapshot.securities),
                content_sha256,
                payload_json,
                snapshot.available_as_of_time.isoformat(),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _build_snapshot(
    *,
    actual_data_date: date,
    source: str,
    source_published_at: datetime,
    securities: tuple[EligibleSecurity, ...],
) -> EligibleUniverseSnapshot:
    identity = json.dumps(
        {
            "actual_data_date": actual_data_date.isoformat(),
            "source": source,
            "source_published_at": source_published_at.isoformat(),
            "eligibility_rules_version": ELIGIBILITY_RULES_VERSION,
            "securities": [asdict(item) for item in securities],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return EligibleUniverseSnapshot(
        universe_version=f"main-board:{actual_data_date}:{digest[:20]}",
        available_as_of_time=source_published_at,
        actual_data_date=actual_data_date,
        source=source,
        source_published_at=source_published_at,
        eligibility_rules_version=ELIGIBILITY_RULES_VERSION,
        minimum_listing_trading_days=MINIMUM_LISTING_TRADING_DAYS,
        securities=securities,
    )


def _is_eligible(code: str, name: str) -> bool:
    normalized_name = name.upper().replace(" ", "")
    return (
        MAIN_BOARD_CODE.fullmatch(code) is not None
        and "ST" not in normalized_name
        and "退" not in normalized_name
    )


def _snapshot_json(snapshot: EligibleUniverseSnapshot) -> str:
    return json.dumps(
        {
            **asdict(snapshot),
            "available_as_of_time": snapshot.available_as_of_time.isoformat(),
            "actual_data_date": snapshot.actual_data_date.isoformat(),
            "source_published_at": snapshot.source_published_at.isoformat(),
            "securities": [asdict(item) for item in snapshot.securities],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _snapshot_from_json(payload_json: str) -> EligibleUniverseSnapshot:
    payload = cast(dict[str, object], json.loads(payload_json))
    rows = cast(list[dict[str, object]], payload["securities"])
    return EligibleUniverseSnapshot(
        universe_version=cast(str, payload["universe_version"]),
        available_as_of_time=datetime.fromisoformat(
            cast(str, payload["available_as_of_time"])
        ),
        actual_data_date=date.fromisoformat(cast(str, payload["actual_data_date"])),
        source=cast(str, payload["source"]),
        source_published_at=datetime.fromisoformat(
            cast(str, payload["source_published_at"])
        ),
        eligibility_rules_version=cast(
            str,
            payload["eligibility_rules_version"],
        ),
        minimum_listing_trading_days=cast(
            int,
            payload["minimum_listing_trading_days"],
        ),
        securities=tuple(
            EligibleSecurity(
                code=cast(str, row["code"]),
                name=cast(str, row["name"]),
                listing_trading_days=cast(int, row["listing_trading_days"]),
            )
            for row in rows
        ),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of_time 必须包含时区")
