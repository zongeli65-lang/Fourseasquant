from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping

from fourseasquant.industry_chain.validation import (
    IndustryChainValidationError,
    _boolean,
    _mapping,
    _normalize_json,
    _parse_datetime,
    _require_keys,
    _text,
)


@dataclass(frozen=True)
class ValidatedSelection:
    snapshot: dict[str, object]
    snapshot_json: str
    snapshot_sha256: str
    selection_id: str
    selection_version: int
    run_id: str
    event_id: str
    event: dict[str, object]
    event_json: str
    event_sha256: str
    evidence_refs: tuple[str, ...]
    trigger_method: str
    as_of_time: datetime
    started_at: datetime
    completed_at: datetime
    status: str
    candidate_count: int
    candidate_codes: tuple[str, ...]
    rules_version: str
    model: dict[str, object]
    prompt_version: str
    knowledge_version: str
    stock_universe_version: str


_TOP_LEVEL_FIELDS = {
    "schema_version",
    "selection_id",
    "selection_version",
    "event_id",
    "rules_version",
    "model",
    "prompt_version",
    "knowledge_version",
    "stock_universe_version",
    "trigger_method",
    "as_of_time",
    "started_at",
    "completed_at",
    "status",
    "decision_authority",
    "immutable",
    "contains_trading_instruction",
    "event",
    "candidates",
    "summary",
    "unresolved_items",
}
_MODEL_FIELDS = {
    "model_id",
    "model_revision",
    "quantization",
    "runtime_id",
    "temperature",
}
_EVENT_FIELDS = {
    "title",
    "event_time",
    "trigger_types",
    "affected_product_or_service",
    "gap_node",
    "industry_evidence_refs",
    "policy_only",
}
_CANDIDATE_FIELDS = {
    "rank",
    "code",
    "name",
    "chain_node",
    "benefit_type",
    "profit_transmission_path",
    "business_materiality",
    "ownership_relation",
    "realization",
    "industry_evidence_refs",
    "company_evidence_refs",
    "uncertainties",
}
_BUSINESS_FIELDS = {
    "main_business_confirmed",
    "disclosure_mode",
    "revenue_share",
    "gross_profit_share",
    "official_qualitative_basis",
    "evidence_refs",
}
_OWNERSHIP_FIELDS = {
    "relation_type",
    "holding_share",
    "attributable_profit_share",
    "official_qualitative_basis",
    "evidence_refs",
}
_REALIZATION_FIELDS = {"status", "expected_start", "basis", "evidence_refs"}


def validate_selection(snapshot: Mapping[str, object]) -> ValidatedSelection:
    normalized = _normalize_json(snapshot)
    _require_keys(
        normalized,
        required=_TOP_LEVEL_FIELDS,
        optional=set(),
        label="selection",
    )
    if normalized["schema_version"] != "industry-chain-selection-v0.1":
        raise IndustryChainValidationError("候选快照契约版本不受支持")
    if normalized["decision_authority"] != "candidate_selection_only":
        raise IndustryChainValidationError("模型只能拥有候选选取权")
    if _boolean(normalized["immutable"], "immutable") is not True:
        raise IndustryChainValidationError("候选快照必须不可修改")
    if _boolean(
        normalized["contains_trading_instruction"],
        "contains_trading_instruction",
    ):
        raise IndustryChainValidationError("候选快照不得包含交易指令")

    selection_id = _text(normalized["selection_id"], "selection_id")
    selection_version = _positive_integer(
        normalized["selection_version"],
        "selection_version",
    )
    event_id = _text(normalized["event_id"], "event_id")
    model = _mapping(normalized["model"], "model")
    _require_keys(model, required=_MODEL_FIELDS, optional=set(), label="model")
    for field in _MODEL_FIELDS - {"temperature"}:
        _text(model[field], f"model.{field}")
    _temperature(model["temperature"])

    as_of_time = _parse_datetime(normalized["as_of_time"], "as_of_time")
    started_at = _parse_datetime(normalized["started_at"], "started_at")
    completed_at = _parse_datetime(normalized["completed_at"], "completed_at")
    if completed_at < started_at:
        raise IndustryChainValidationError("completed_at 不能早于 started_at")

    event = _mapping(normalized["event"], "event")
    _require_keys(event, required=_EVENT_FIELDS, optional=set(), label="event")
    event_time = _parse_datetime(event["event_time"], "event.event_time")
    if event_time > as_of_time:
        raise IndustryChainValidationError("事件时间晚于 as_of_time")
    if _boolean(event["policy_only"], "event.policy_only"):
        raise IndustryChainValidationError("纯政策事件不能形成候选快照")
    _text(event["title"], "event.title")
    _text(event["affected_product_or_service"], "event.affected_product_or_service")
    _text(event["gap_node"], "event.gap_node")
    event_refs = _string_list(
        event["industry_evidence_refs"],
        "event.industry_evidence_refs",
        minimum=1,
    )
    _string_list(event["trigger_types"], "event.trigger_types", minimum=1)

    candidates = normalized["candidates"]
    if not isinstance(candidates, list):
        raise IndustryChainValidationError("candidates 必须是列表")
    refs = set(event_refs)
    ranks: list[int] = []
    candidate_codes: list[str] = []
    for index, value in enumerate(candidates):
        candidate, candidate_refs, rank = _validate_candidate(
            value,
            index,
            as_of_time=as_of_time,
        )
        refs.update(candidate_refs)
        ranks.append(rank)
        candidate_codes.append(_text(candidate["code"], "candidate.code"))
        candidates[index] = candidate
    if ranks != list(range(1, len(candidates) + 1)):
        raise IndustryChainValidationError("候选 rank 必须从 1 连续排列")

    status = _text(normalized["status"], "status")
    if candidates and status != "selected":
        raise IndustryChainValidationError("存在候选时 status 必须为 selected")
    if not candidates and status not in {"empty", "evidence_insufficient"}:
        raise IndustryChainValidationError("空候选状态不合法")
    _string_list(normalized["unresolved_items"], "unresolved_items")
    _text(normalized["summary"], "summary")

    snapshot_json = _canonical_json(normalized)
    event_json = _canonical_json(event)
    return ValidatedSelection(
        snapshot=normalized,
        snapshot_json=snapshot_json,
        snapshot_sha256=_sha256(snapshot_json),
        selection_id=selection_id,
        selection_version=selection_version,
        run_id=f"{selection_id}:v{selection_version}",
        event_id=event_id,
        event=event,
        event_json=event_json,
        event_sha256=_sha256(event_json),
        evidence_refs=tuple(sorted(refs)),
        trigger_method=_text(normalized["trigger_method"], "trigger_method"),
        as_of_time=as_of_time,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        candidate_count=len(candidates),
        candidate_codes=tuple(candidate_codes),
        rules_version=_text(normalized["rules_version"], "rules_version"),
        model=model,
        prompt_version=_text(normalized["prompt_version"], "prompt_version"),
        knowledge_version=_text(
            normalized["knowledge_version"],
            "knowledge_version",
        ),
        stock_universe_version=_text(
            normalized["stock_universe_version"],
            "stock_universe_version",
        ),
    )


def _validate_candidate(
    value: object,
    index: int,
    *,
    as_of_time: datetime,
) -> tuple[dict[str, object], set[str], int]:
    candidate = _mapping(value, f"candidates[{index}]")
    _require_keys(
        candidate,
        required=_CANDIDATE_FIELDS,
        optional=set(),
        label=f"candidates[{index}]",
    )
    rank = _positive_integer(candidate["rank"], "candidate.rank")
    code = _text(candidate["code"], "candidate.code")
    if re.fullmatch(r"\d{6}", code) is None:
        raise IndustryChainValidationError("候选证券代码必须为 6 位数字")
    for field in {"name", "chain_node"}:
        _text(candidate[field], f"candidate.{field}")
    _enum_text(
        candidate["benefit_type"],
        {"direct", "indirect", "conditional"},
        "candidate.benefit_type",
    )
    _string_list(candidate["profit_transmission_path"], "profit_transmission_path", 3)
    refs = set(
        _string_list(candidate["industry_evidence_refs"], "industry_refs", 1)
    )
    refs.update(
        _string_list(candidate["company_evidence_refs"], "company_refs", 1)
    )
    _string_list(candidate["uncertainties"], "uncertainties")

    business = _mapping(candidate["business_materiality"], "business_materiality")
    _require_keys(business, required=_BUSINESS_FIELDS, optional=set(), label="business")
    if not _boolean(business["main_business_confirmed"], "main_business_confirmed"):
        raise IndustryChainValidationError("候选相关产品必须属于实质主营")
    disclosure_mode = _enum_text(
        business["disclosure_mode"],
        {"quantitative", "official_qualitative", "mixed"},
        "business.disclosure_mode",
    )
    revenue_share = _optional_share(
        business["revenue_share"],
        "business.revenue_share",
    )
    gross_profit_share = _optional_share(
        business["gross_profit_share"],
        "business.gross_profit_share",
    )
    qualitative_basis = _optional_nonempty_text(
        business["official_qualitative_basis"],
        "business.official_qualitative_basis",
    )
    _validate_business_materiality(
        disclosure_mode=disclosure_mode,
        revenue_share=revenue_share,
        gross_profit_share=gross_profit_share,
        qualitative_basis=qualitative_basis,
    )
    refs.update(_string_list(business["evidence_refs"], "business.evidence_refs", 1))

    ownership = _mapping(candidate["ownership_relation"], "ownership_relation")
    _require_keys(ownership, required=_OWNERSHIP_FIELDS, optional=set(), label="ownership")
    relation_type = _enum_text(
        ownership["relation_type"],
        {
            "listed_company_direct",
            "consolidated_subsidiary",
            "non_consolidated_associate",
        },
        "ownership.relation_type",
    )
    holding_share = _optional_share(
        ownership["holding_share"],
        "ownership.holding_share",
    )
    attributable_profit_share = _optional_share(
        ownership["attributable_profit_share"],
        "ownership.attributable_profit_share",
    )
    ownership_basis = _optional_nonempty_text(
        ownership["official_qualitative_basis"],
        "ownership.official_qualitative_basis",
    )
    _validate_ownership(
        relation_type=relation_type,
        holding_share=holding_share,
        attributable_profit_share=attributable_profit_share,
        qualitative_basis=ownership_basis,
    )
    refs.update(_string_list(ownership["evidence_refs"], "ownership.evidence_refs", 1))

    realization = _mapping(candidate["realization"], "realization")
    _require_keys(
        realization,
        required=_REALIZATION_FIELDS,
        optional=set(),
        label="realization",
    )
    _text(realization["basis"], "realization.basis")
    realization_status = _enum_text(
        realization["status"],
        {"current", "within_one_quarter", "conditional_within_one_quarter"},
        "realization.status",
    )
    expected_start = _optional_date(
        realization["expected_start"],
        "realization.expected_start",
    )
    if realization_status != "current" and expected_start is None:
        raise IndustryChainValidationError("未来兑现候选必须提供 expected_start")
    if (
        expected_start is not None
        and expected_start > as_of_time.date() + timedelta(days=92)
    ):
        raise IndustryChainValidationError("候选兑现时间超过一个季度")
    refs.update(
        _string_list(realization["evidence_refs"], "realization.evidence_refs", 1)
    )
    return candidate, refs, rank


def _string_list(value: object, label: str, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise IndustryChainValidationError(f"{label} 数量不足")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)):
        raise IndustryChainValidationError(f"{label} 不得重复")
    return result


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IndustryChainValidationError(f"{label} 必须为正整数")
    return value


def _temperature(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndustryChainValidationError("model.temperature 必须是数字")
    result = float(value)
    if not 0 <= result <= 2:
        raise IndustryChainValidationError("model.temperature 超出范围")
    return result


def _optional_share(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndustryChainValidationError(f"{label} 必须是 0 至 1 的数字或 null")
    result = float(value)
    if not 0 <= result <= 1:
        raise IndustryChainValidationError(f"{label} 必须在 0 至 1 之间")
    return result


def _optional_nonempty_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _optional_date(value: object, label: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(_text(value, label))
    except ValueError as error:
        raise IndustryChainValidationError(f"{label} 不是有效日期") from error


def _enum_text(value: object, allowed: set[str], label: str) -> str:
    result = _text(value, label)
    if result not in allowed:
        raise IndustryChainValidationError(f"{label} 不在允许范围内")
    return result


def _validate_business_materiality(
    *,
    disclosure_mode: str,
    revenue_share: float | None,
    gross_profit_share: float | None,
    qualitative_basis: str | None,
) -> None:
    disclosed = tuple(
        share
        for share in (revenue_share, gross_profit_share)
        if share is not None
    )
    if any(share < 0.2 for share in disclosed):
        raise IndustryChainValidationError("已披露的主营占比低于 20%")
    if disclosure_mode == "quantitative":
        if revenue_share is None or gross_profit_share is None:
            raise IndustryChainValidationError("量化主营必须同时提供收入和毛利润占比")
        if max(revenue_share, gross_profit_share) < 0.3:
            raise IndustryChainValidationError("量化主营至少一项占比必须达到 30%")
        return
    if qualitative_basis is None:
        raise IndustryChainValidationError("定性或混合主营必须提供正式定性依据")
    if disclosure_mode == "mixed" and disclosed and max(disclosed) < 0.3:
        raise IndustryChainValidationError("混合主营已披露占比未达到 30%")


def _validate_ownership(
    *,
    relation_type: str,
    holding_share: float | None,
    attributable_profit_share: float | None,
    qualitative_basis: str | None,
) -> None:
    if relation_type == "listed_company_direct":
        if qualitative_basis is None:
            raise IndustryChainValidationError("上市公司直接经营必须提供正式依据")
        return
    if relation_type == "consolidated_subsidiary":
        if holding_share is not None and holding_share < 0.5:
            raise IndustryChainValidationError("并表子公司持股比例低于 50%")
        if holding_share is None and qualitative_basis is None:
            raise IndustryChainValidationError("并表关系缺少持股比例或控制依据")
        return
    disclosed = tuple(
        share
        for share in (holding_share, attributable_profit_share)
        if share is not None
    )
    if any(share < 0.2 for share in disclosed):
        raise IndustryChainValidationError("参股关系已披露比例低于 20%")
    if len(disclosed) < 2 and qualitative_basis is None:
        raise IndustryChainValidationError("参股关系缺少持股、利润贡献或正式定性依据")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
