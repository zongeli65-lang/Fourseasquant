from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, cast
from urllib.parse import urlparse


class IndustryChainValidationError(ValueError):
    """产业链输入不符合冻结契约。"""


class FutureEvidenceError(IndustryChainValidationError):
    """证据发布时间晚于当次分析时间。"""


@dataclass(frozen=True)
class ValidatedEvidence:
    snapshot: dict[str, object]
    snapshot_json: str
    snapshot_sha256: str
    evidence_id: str
    event_id: str
    source: dict[str, object]
    headline: str
    published_at: datetime
    updated_at: datetime | None
    collected_at: datetime
    content_sha256: str
    language: str
    is_reprint: bool
    reprint_cluster_id: str | None
    retention_mode: str
    original_file_path: str | None


_EVIDENCE_REQUIRED = {
    "schema_version",
    "evidence_id",
    "event_id",
    "source",
    "headline",
    "published_at",
    "collected_at",
    "content_sha256",
    "language",
    "is_reprint",
    "retention_mode",
    "facts",
}
_EVIDENCE_OPTIONAL = {
    "updated_at",
    "reprint_cluster_id",
    "original_file_path",
}
_SOURCE_FIELDS = {
    "source_id",
    "source_name",
    "source_tier",
    "source_type",
    "url",
    "is_primary",
    "access_class",
}
_FACT_REQUIRED = {
    "fact_id",
    "fact_type",
    "subject",
    "statement",
    "disclosure_mode",
    "excerpt",
    "retention_reason",
}
_FACT_OPTIONAL = {"numeric_value", "unit", "period"}
_SOURCE_TYPES = {
    "company_filing",
    "exchange",
    "regulator",
    "government",
    "statistics",
    "industry_association",
    "company_official",
    "financial_media",
    "industry_media",
    "aggregator",
    "forum",
    "social",
    "local_database",
    "other",
}
_ACCESS_CLASSES = {
    "public_no_login",
    "user_provided_licensed",
    "inaccessible",
}
_RETENTION_MODES = {
    "metadata_only",
    "model_selected_excerpts",
    "official_original",
}


def validate_evidence(
    snapshot: Mapping[str, object],
    *,
    as_of_time: datetime,
) -> ValidatedEvidence:
    _require_aware(as_of_time, "as_of_time")
    normalized = _normalize_json(snapshot)
    _require_keys(
        normalized,
        required=_EVIDENCE_REQUIRED,
        optional=_EVIDENCE_OPTIONAL,
        label="evidence",
    )
    if normalized["schema_version"] != "industry-chain-evidence-v0.1":
        raise IndustryChainValidationError("证据契约版本不受支持")

    source = _mapping(normalized["source"], "source")
    _require_keys(source, required=_SOURCE_FIELDS, optional=set(), label="source")
    _validate_source(source)
    _validate_facts(normalized["facts"])

    published_at = _parse_datetime(normalized["published_at"], "published_at")
    collected_at = _parse_datetime(normalized["collected_at"], "collected_at")
    updated_value = normalized.get("updated_at")
    updated_at = (
        None
        if updated_value is None
        else _parse_datetime(updated_value, "updated_at")
    )
    if published_at > as_of_time or (
        updated_at is not None and updated_at > as_of_time
    ):
        raise FutureEvidenceError("证据包含 as_of_time 之后才公开的信息")
    if collected_at < published_at:
        raise IndustryChainValidationError("采集时间不能早于发布时间")

    content_sha256 = _text(normalized["content_sha256"], "content_sha256")
    if re.fullmatch(r"[a-f0-9]{64}", content_sha256) is None:
        raise IndustryChainValidationError("content_sha256 格式错误")
    snapshot_json = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ValidatedEvidence(
        snapshot=normalized,
        snapshot_json=snapshot_json,
        snapshot_sha256=hashlib.sha256(snapshot_json.encode()).hexdigest(),
        evidence_id=_text(normalized["evidence_id"], "evidence_id"),
        event_id=_text(normalized["event_id"], "event_id"),
        source=source,
        headline=_text(normalized["headline"], "headline"),
        published_at=published_at,
        updated_at=updated_at,
        collected_at=collected_at,
        content_sha256=content_sha256,
        language=_text(normalized["language"], "language"),
        is_reprint=_boolean(normalized["is_reprint"], "is_reprint"),
        reprint_cluster_id=_optional_text(normalized.get("reprint_cluster_id")),
        retention_mode=_enum(
            normalized["retention_mode"],
            _RETENTION_MODES,
            "retention_mode",
        ),
        original_file_path=_optional_text(normalized.get("original_file_path")),
    )


def _validate_source(source: dict[str, object]) -> None:
    _text(source["source_id"], "source_id")
    _text(source["source_name"], "source_name")
    tier = source["source_tier"]
    if isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 5:
        raise IndustryChainValidationError("source_tier 必须为 1 至 5")
    _enum(source["source_type"], _SOURCE_TYPES, "source_type")
    url = _text(source["url"], "url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IndustryChainValidationError("source.url 必须为 HTTP(S) 地址")
    _boolean(source["is_primary"], "is_primary")
    _enum(source["access_class"], _ACCESS_CLASSES, "access_class")


def _validate_facts(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise IndustryChainValidationError("facts 必须是非空数组")
    for index, item in enumerate(value):
        fact = _mapping(item, f"facts[{index}]")
        _require_keys(
            fact,
            required=_FACT_REQUIRED,
            optional=_FACT_OPTIONAL,
            label=f"facts[{index}]",
        )
        for field in _FACT_REQUIRED:
            _text(fact[field], f"facts[{index}].{field}")


def _normalize_json(value: Mapping[str, object]) -> dict[str, object]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise IndustryChainValidationError("输入必须是有效 JSON") from error
    return cast(dict[str, object], decoded)


def _require_keys(
    value: dict[str, object],
    *,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise IndustryChainValidationError(f"{label} 缺少字段：{sorted(missing)}")
    if extra:
        raise IndustryChainValidationError(f"{label} 包含未知字段：{sorted(extra)}")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise IndustryChainValidationError(f"{label} 必须是对象")
    return cast(dict[str, object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndustryChainValidationError(f"{label} 必须是非空字符串")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _text(value, "可选文本字段")


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise IndustryChainValidationError(f"{label} 必须是布尔值")
    return value


def _enum(value: object, allowed: set[str], label: str) -> str:
    text = _text(value, label)
    if text not in allowed:
        raise IndustryChainValidationError(f"{label} 不在允许范围内")
    return text


def _parse_datetime(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, label).replace("Z", "+00:00"))
    except ValueError as error:
        raise IndustryChainValidationError(f"{label} 不是有效时间") from error
    _require_aware(parsed, label)
    return parsed


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndustryChainValidationError(f"{label} 必须包含时区")
