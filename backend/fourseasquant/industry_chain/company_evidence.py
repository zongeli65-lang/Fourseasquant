from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from fourseasquant.fundamental_mechanical import RULES_VERSION
from fourseasquant.industry_chain.universe import EligibleUniverseSnapshot
from fourseasquant.sqlite_connection import open_database_connection


MAX_CODES_PER_QUERY = 50
BEIJING = ZoneInfo("Asia/Shanghai")
GENERIC_RECALL_TERMS = {
    "产业链",
    "供应",
    "供给",
    "需求",
    "增长",
    "下降",
    "利好",
    "订单",
    "产能",
    "公司",
    "产品",
    "服务",
}
RECALL_ALIAS_GROUPS = (
    ("电芯", "电池", "锂电", "锂离子电池", "储能电池", "动力电池"),
    ("芯片", "半导体", "集成电路", "处理器"),
    ("光伏", "太阳能", "光伏发电"),
    ("风电", "风力发电"),
    ("稀土", "稀土永磁", "永磁材料", "磁材"),
    ("光伏玻璃", "玻璃"),
)
EvidenceAvailability = Literal["available", "partial", "unavailable"]


class StockScopeViolation(ValueError):
    """查询包含股票范围之外的代码。"""


@dataclass(frozen=True)
class LocalCompanyEvidence:
    code: str
    name: str
    universe_version: str
    availability: EvidenceAvailability
    evidence_as_of_date: date | None
    published_at: datetime | None
    rules_version: str
    main_business_name: str | None
    gross_profit_share: float | None
    revenue_share: float | None
    source_urls: tuple[str, ...]
    ownership_evidence_available: bool
    limitations: tuple[str, ...]


class SqliteCompanyEvidenceAdapter:
    """只读查询已发布的本地主营证据，不读取技术或投资排序。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def query(
        self,
        *,
        universe: EligibleUniverseSnapshot,
        codes: list[str],
        as_of_time: datetime,
    ) -> tuple[LocalCompanyEvidence, ...]:
        _require_aware(as_of_time)
        normalized_as_of = as_of_time.astimezone(BEIJING)
        if len(codes) > MAX_CODES_PER_QUERY:
            raise ValueError(f"单次最多查询 {MAX_CODES_PER_QUERY} 只股票")
        if len(codes) != len(set(codes)):
            raise ValueError("查询代码不能重复")
        outside = sorted(set(codes) - universe.codes)
        if outside:
            raise StockScopeViolation(f"股票不在合格范围：{outside}")
        names = {item.code: item.name for item in universe.securities}
        with open_database_connection(self._path) as connection:
            return tuple(
                self._query_one(
                    connection,
                    code=code,
                    name=names[code],
                    universe_version=universe.universe_version,
                    as_of_time=normalized_as_of,
                )
                for code in codes
            )

    def recall(
        self,
        *,
        universe: EligibleUniverseSnapshot,
        terms: list[str],
        corpus: list[str],
        as_of_time: datetime,
        limit: int,
    ) -> tuple[LocalCompanyEvidence, ...]:
        _require_aware(as_of_time)
        if not 1 <= limit <= MAX_CODES_PER_QUERY:
            raise ValueError(
                f"候选召回上限必须在 1 到 {MAX_CODES_PER_QUERY} 之间"
            )
        normalized_as_of = as_of_time.astimezone(BEIJING)
        normalized_terms = _normalized_recall_terms(terms)
        corpus_text = " ".join(corpus)
        name_scores = {
            item.code: 100 + len(item.name)
            for item in universe.securities
            if len(item.name.strip()) >= 2 and item.name in corpus_text
        }
        with open_database_connection(self._path) as connection:
            business_rows = _business_recall_rows(
                connection,
                universe=universe,
                as_of_time=normalized_as_of,
            )
            scores: dict[str, int] = {}
            for code, main_business_name in business_rows:
                normalized_business = _normalize_recall_text(
                    main_business_name
                )
                matches = [
                    term
                    for term in normalized_terms
                    if term in normalized_business
                    or normalized_business in term
                ]
                if matches:
                    scores[code] = (
                        sum(len(term) for term in matches)
                        + name_scores.get(code, 0)
                    )
            ordered_codes = [
                code
                for code, _ in sorted(
                    scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:limit]
            ]
            names = {
                item.code: item.name for item in universe.securities
            }
            return tuple(
                self._query_one(
                    connection,
                    code=code,
                    name=names[code],
                    universe_version=universe.universe_version,
                    as_of_time=normalized_as_of,
                )
                for code in ordered_codes
            )

    def _query_one(
        self,
        connection: sqlite3.Connection,
        *,
        code: str,
        name: str,
        universe_version: str,
        as_of_time: datetime,
    ) -> LocalCompanyEvidence:
        batch = connection.execute(
            """
            SELECT batch.as_of_date, batch.published_at
            FROM personal_fundamental_monthly_batches AS batch
            JOIN json_each(batch.completed_codes_json) AS completed
              ON completed.value = ?
            WHERE batch.rules_version = ?
              AND batch.status = 'published'
              AND batch.as_of_date <= ?
              AND batch.published_at <= ?
            ORDER BY batch.as_of_date DESC, batch.id DESC
            LIMIT 1
            """,
            (
                code,
                RULES_VERSION,
                as_of_time.date().isoformat(),
                as_of_time.isoformat(),
            ),
        ).fetchone()
        if batch is None:
            return _unavailable(
                code=code,
                name=name,
                universe_version=universe_version,
                reason="该时间点之前没有已发布的本地主营快照",
            )
        batch_date = date.fromisoformat(cast(str, batch[0]))
        published_at = datetime.fromisoformat(cast(str, batch[1]))
        rows = connection.execute(
            """
            SELECT as_of_date, changed_fields_json, source_urls_json
            FROM personal_fundamental_monthly_snapshots
            WHERE code = ? AND rules_version = ?
              AND as_of_date <= ?
              AND created_at <= ?
            ORDER BY as_of_date
            """,
            (
                code,
                RULES_VERSION,
                batch_date.isoformat(),
                as_of_time.isoformat(),
            ),
        ).fetchall()
        main_business_name: str | None = None
        gross_profit_share: float | None = None
        evidence_as_of_date: date | None = None
        source_urls: set[str] = set()
        for row in rows:
            changed = cast(dict[str, object], json.loads(cast(str, row[1])))
            relevant_change = False
            if "main_business_name" in changed:
                main_business_name = cast(str | None, changed["main_business_name"])
                relevant_change = True
            if "main_business_profit_share" in changed:
                value = changed["main_business_profit_share"]
                gross_profit_share = (
                    float(cast(float | int, value)) if value is not None else None
                )
                relevant_change = True
            if relevant_change:
                evidence_as_of_date = date.fromisoformat(cast(str, row[0]))
                source_urls = set(json.loads(cast(str, row[2])))

        if main_business_name is not None and gross_profit_share is not None:
            availability: EvidenceAvailability = "available"
        elif main_business_name is not None:
            availability = "partial"
        else:
            return _unavailable(
                code=code,
                name=name,
                universe_version=universe_version,
                reason="本地快照没有可用的主营业务字段",
                published_at=published_at,
            )
        limitations = [
            "本地快照未保存主营收入占比",
            "本地快照未保存子公司、参股公司或客户关系证据",
            "来源网址只能作为检索入口，正式入选仍需保存原始证据片段",
        ]
        if availability == "partial":
            limitations.append("公司未形成可用的主营毛利润占比")
        return LocalCompanyEvidence(
            code=code,
            name=name,
            universe_version=universe_version,
            availability=availability,
            evidence_as_of_date=evidence_as_of_date,
            published_at=published_at,
            rules_version=RULES_VERSION,
            main_business_name=main_business_name,
            gross_profit_share=gross_profit_share,
            revenue_share=None,
            source_urls=tuple(sorted(source_urls)),
            ownership_evidence_available=False,
            limitations=tuple(limitations),
        )


def _unavailable(
    *,
    code: str,
    name: str,
    universe_version: str,
    reason: str,
    published_at: datetime | None = None,
) -> LocalCompanyEvidence:
    return LocalCompanyEvidence(
        code=code,
        name=name,
        universe_version=universe_version,
        availability="unavailable",
        evidence_as_of_date=None,
        published_at=published_at,
        rules_version=RULES_VERSION,
        main_business_name=None,
        gross_profit_share=None,
        revenue_share=None,
        source_urls=(),
        ownership_evidence_available=False,
        limitations=(reason,),
    )


def _business_recall_rows(
    connection: sqlite3.Connection,
    *,
    universe: EligibleUniverseSnapshot,
    as_of_time: datetime,
) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        """
        SELECT snapshot.code, snapshot.changed_fields_json
        FROM personal_fundamental_monthly_snapshots AS snapshot
        WHERE snapshot.rules_version = ?
          AND snapshot.as_of_date <= ?
          AND snapshot.created_at <= ?
          AND EXISTS (
              SELECT 1
              FROM personal_fundamental_monthly_batches AS batch,
                   json_each(batch.completed_codes_json) AS completed
              WHERE batch.rules_version = snapshot.rules_version
                AND batch.status = 'published'
                AND batch.as_of_date <= ?
                AND batch.published_at <= ?
                AND completed.value = snapshot.code
          )
        ORDER BY snapshot.code, snapshot.as_of_date, snapshot.created_at
        """,
        (
            RULES_VERSION,
            as_of_time.date().isoformat(),
            as_of_time.isoformat(),
            as_of_time.date().isoformat(),
            as_of_time.isoformat(),
        ),
    ).fetchall()
    allowed_codes = universe.codes
    latest: dict[str, str] = {}
    for raw_code, raw_changed in rows:
        code = cast(str, raw_code)
        if code not in allowed_codes:
            continue
        changed = cast(dict[str, object], json.loads(cast(str, raw_changed)))
        value = changed.get("main_business_name")
        if isinstance(value, str) and value.strip():
            latest[code] = value.strip()
    return tuple(sorted(latest.items()))


def _normalized_recall_terms(terms: list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for term in terms:
        value = _normalize_recall_text(term)
        if len(value) < 2 or value in GENERIC_RECALL_TERMS:
            continue
        normalized.append(value)
        for group in RECALL_ALIAS_GROUPS:
            if any(alias in value for alias in group):
                normalized.extend(group)
    return tuple(dict.fromkeys(normalized))


def _normalize_recall_text(value: str) -> str:
    return re.sub(
        r"[\s,，。；;、/\\()（）\-]+",
        "",
        value.lower(),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of_time 必须包含时区")
