from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .company_evidence import LocalCompanyEvidence
from .control import HuntingRequestRecord
from .discovery import DiscoveryItem, read_discovery_items
from .deep_hunt_audit import record_deep_hunt_audit
from .entity_matching import company_name_mentioned
from .local_data import LocalIndustryChainData
from .ollama_runtime import OllamaStructuredRuntime, StructuredModelResult
from .policy import ACTIVE_SELECTION_RULES_VERSION
from .repository import IndustryChainRepository, PublishSelectionResult
from .source_registry import SOURCE_CONFIG_VERSION, RegisteredSource, read_latest_sources


RULES_VERSION = ACTIVE_SELECTION_RULES_VERSION
PROMPT_VERSION = "industry-chain-hunt-prompt-v0.4"
KNOWLEDGE_VERSION = "industry-chain-knowledge-v0.1"
SCHEMA_VERSION = "industry-chain-selection-v0.1"
MAX_FOCUSED_COMPANIES_PER_BATCH = 6
MAX_EVENT_PROMPT_ITEMS = 10
MAX_COMPANY_DOSSIER_ITEMS = 3
URL_DATE_PATTERN = re.compile(
    r"(20\d{2})(?:[-_/]?)(\d{2})(?:[-_/]?)(\d{2})"
)
MAX_SOURCE_EVENT_AGE = timedelta(hours=72)
MAX_COMPANY_REALIZATION_EVIDENCE_AGE = timedelta(days=92)
NEGATED_EVIDENCE = re.compile(
    r"(未披露|未明确|没有|尚无|无法|不确定|不具备|未形成|假设|有望)"
)
POSITIVE_REALIZATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"签订.{0,20}(合同|订单)",
        r"(新增|在手|确认|获得).{0,12}订单",
        r"新签.{0,24}(合同|订单)",
        r"订单.{0,16}(合计|金额|排产至)",
        r"订单.{0,12}(增长|增加|饱满|落地)",
        r"(已供货|已经供货|实现供货|批量供货|批量交付|开始交付|正式交付)",
        r"(销量|销售量|发电量|上网电量|利用小时|营业收入|销售收入|"
        r"毛利润|净利润).{0,16}(增长|增加|提升|同比|环比|达到|实现)",
        r"(产能利用率|开工率).{0,12}(提升|上升|达到|超过|满负荷)",
        r"(涨价|提价|价格上涨|售价上涨|供不应求|满产满销)",
        r"客户.{0,16}(采购|下单|导入|验收)",
    )
)
SUPPLY_DEMAND_EVIDENCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(需求|订单|采购量).{0,16}(增长|增加|提升|旺盛|超预期)",
        r"(供给|供应|产量).{0,16}(收缩|减少|下降|中断|短缺)",
        r"(停产|暂停生产|减产|限产|供应中断|供应短缺|缺货)",
        r"(库存|库销比).{0,16}(下降|去化|减少|耗尽|低位)",
        r"(涨价|提价|价格上涨|售价上涨|报价上调|供不应求)",
        r"(签订|新签|获得).{0,20}(合同|订单)",
        r"(批量供货|批量交付|正式交付|开始交付)",
    )
)
COMMERCIAL_READINESS_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(规模化量产|商业化量产|规模化生产|已实现量产)",
        r"(稳定供应|持续供应|持续供货|稳定供货)",
        r"(实现销售|形成销售|商业化销售|持续销售)",
        r"客户.{0,16}(覆盖|合作|批量采购|稳定采购)",
        r"(产品|业务).{0,16}(进入供应链|纳入采购目录)",
    )
)
FORMAL_COUNTER_EVIDENCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(尚未|未|没有|尚无).{0,18}(签订|获得|形成).{0,10}(订单|合同|销售|收入)",
        r"(尚未|未|没有|尚无).{0,18}(批量供货|批量交付|正式交付|规模化量产)",
        r"(仅|尚处于|仍处于).{0,10}(送样|验证|测试|研发|规划|建设)",
        r"(客户|订单|销量|销售收入).{0,18}(下降|减少|流失|取消)",
    )
)

TriggerType = Literal[
    "supply_contraction",
    "demand_increase",
    "inventory_depletion",
    "order_delivery",
    "price_change",
    "company_order",
    "other_supply_demand",
]


class CandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=80)
    chain_node: str = Field(min_length=1, max_length=120)
    benefit_type: Literal["direct", "indirect", "conditional"]
    profit_transmission_path: list[str] = Field(min_length=3, max_length=8)
    main_business_match: bool
    realization_status: Literal[
        "current",
        "within_one_quarter",
        "conditional_within_one_quarter",
    ]
    expected_start: str = Field(max_length=10)
    realization_basis: str = Field(min_length=1, max_length=500)
    uncertainties: list[str] = Field(max_length=8)
    relation_type: Literal[
        "listed_company_direct",
        "consolidated_subsidiary",
        "equity_method_investee",
    ] = "listed_company_direct"
    related_entity: str = Field(default="", max_length=120)
    holding_share: float | None = Field(default=None, ge=0, le=1)
    attributable_profit_share: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    ownership_basis: str = Field(default="", max_length=500)


class DeepHuntDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_valid: bool
    policy_only: bool
    title: str = Field(min_length=1, max_length=200)
    event_time: str = Field(min_length=1, max_length=40)
    trigger_types: list[TriggerType] = Field(max_length=4)
    affected_product_or_service: str = Field(max_length=120)
    gap_node: str = Field(max_length=120)
    candidates: list[CandidateProposal]
    summary: str = Field(min_length=1, max_length=1000)
    unresolved_items: list[str]

    @model_validator(mode="after")
    def validate_policy_and_candidates(self) -> DeepHuntDecision:
        if (not self.event_valid or self.policy_only) and self.candidates:
            raise ValueError("无效或纯政策事件不能包含候选")
        if self.event_valid and not self.trigger_types:
            raise ValueError("有效事件必须包含至少一个供需触发类型")
        if self.event_valid and not self.affected_product_or_service.strip():
            raise ValueError("有效事件必须包含受影响产品或服务")
        if self.event_valid and not self.gap_node.strip():
            raise ValueError("有效事件必须包含产业链缺口节点")
        datetime.fromisoformat(self.event_time.replace("Z", "+00:00"))
        for candidate in self.candidates:
            if candidate.expected_start:
                date.fromisoformat(candidate.expected_start)
        return self


@dataclass(frozen=True)
class DeepHuntResult:
    decision: DeepHuntDecision
    model_result: StructuredModelResult
    publication: PublishSelectionResult | None
    rejected_candidates: tuple[str, ...]


@dataclass(frozen=True)
class CandidateEvidenceChain:
    industry_items: tuple[DiscoveryItem, ...]
    company_product_items: tuple[DiscoveryItem, ...]
    realization_items: tuple[DiscoveryItem, ...]
    counter_items: tuple[DiscoveryItem, ...]
    realization_mode: Literal["direct", "stitched"] | None


class StaleDiscoveryError(ValueError):
    """公开列表页线索的网址日期已经超出补搜窗口。"""


SYSTEM_PROMPT = """
你是产业链供需龙头候选 Agent，只拥有候选选取权，没有最终投资决定权。
禁止使用股价、涨跌幅、技术走势、估值、基本面投资分或模型记忆中的公司事实。
先判断输入是否为已经发生或正在形成的真实供需事件；纯政策只能 policy_only=true 且不得选股。
只能从 eligible_companies 中选择，也可以空选。不得补充名单外公司；不要为了数量上限丢弃符合条件的候选。
候选必须正向受益，相关产品属于实质主营，并有当前或一个季度内开始兑现的订单、客户、销量、利用率、售价、收入或利润证据。
本地主营快照 availability=unavailable 不等于否定；只有 discoveries 中存在该公司的正式披露，并明确相关产品属于主营业务时，才可用网页证据补足。
公司正式披露已经明确相关产品属于主营、已经签单、批量供货、客户采购或销量提升时，即使没有披露具体金额或占比，也必须纳入候选核验，不能仅以“缺少量化数据”为由空选。
行业需求、公司主营和公司兑现可以分别来自不同正式来源，不要求同一篇材料同时写全三段。跨来源拼接时，必须保证受益主体、相关产品、证据时间窗口和传导方向一致，并检查是否存在更新或同期正式反证。
订单或供需兑现发生在子公司、参股公司时，必须填写 relation_type、related_entity、holding_share、attributable_profit_share 和 ownership_basis；没有正式持股或并表证据不得把子公司事件归因给上市公司。
新增技术、扩产或投产只证明供给能力增加，不能单独证明利润上升；还必须找到当期新增需求以及公司销售兑现证据。
利润传导至少写清三段：当期供需变化、公司承接方式、主营收入或毛利润如何增加。每一段都必须来自输入证据，不得用“有望”“假设”补齐。
已经结束的上半年、季度或历史统计只证明过去已经发生的结果，不能自动视为当前至未来一个季度仍然有效；必须有当前新增证据。
板块上涨、震荡反弹、概念异动和“景气向好”只是发现线索，不是供需事件证据。
event_discoveries 是本次事件原始证据，事件标题、时间、产品和触发类型必须以它为核心；company_dossiers 只用于核验公司，绝不能把主营构成资料改写成新的供需事件。
realization_status 为 current 时 expected_start 输出空字符串，其他状态输出 YYYY-MM-DD。
输入没有明确证明公司兑现能力时必须空选；不得把“产品相关”改写成“已向特定客户供货”。
若 eligible_companies 中已有公司同时具备主营证据和需求或销售兑现证据，必须输出候选，不得用笼统的“证据不足”结束。
输出中只写输入能够支持的事实，证据不足写入 unresolved_items。
""".strip()

FOCUSED_CLOSURE_PROMPT = f"""
{SYSTEM_PROMPT}

这是第二轮证据闭环复核。第一轮已经确认事件有效，但没有形成候选。
你必须逐一检查 company_dossiers：
1. 相关产品是否属于主营，可接受公司正式定性披露，不强制要求量化占比；
2. 是否存在当前订单、客户、批量供货、销量、利用率、售价、收入或利润兑现；
3. 能否写出不含假设的三段利润传导。
同时满足时必须输出候选；不满足时必须逐家公司写出具体缺失证据，不能只写“没有证据”。
""".strip()

DEEP_HUNT_OLLAMA_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "event_valid": {"type": "boolean"},
        "policy_only": {"type": "boolean"},
        "title": {"type": "string"},
        "event_time": {"type": "string"},
        "trigger_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "supply_contraction",
                    "demand_increase",
                    "inventory_depletion",
                    "order_delivery",
                    "price_change",
                    "company_order",
                    "other_supply_demand",
                ],
            },
        },
        "affected_product_or_service": {"type": "string"},
        "gap_node": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "chain_node": {"type": "string"},
                    "benefit_type": {
                        "type": "string",
                        "enum": ["direct", "indirect", "conditional"],
                    },
                    "profit_transmission_path": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "main_business_match": {"type": "boolean"},
                    "realization_status": {
                        "type": "string",
                        "enum": [
                            "current",
                            "within_one_quarter",
                            "conditional_within_one_quarter",
                        ],
                    },
                    "expected_start": {"type": "string"},
                    "realization_basis": {"type": "string"},
                    "uncertainties": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "relation_type": {
                        "type": "string",
                        "enum": [
                            "listed_company_direct",
                            "consolidated_subsidiary",
                            "equity_method_investee",
                        ],
                    },
                    "related_entity": {"type": "string"},
                    "holding_share": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "attributable_profit_share": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "ownership_basis": {"type": "string"},
                },
                "required": [
                    "code",
                    "name",
                    "chain_node",
                    "benefit_type",
                    "profit_transmission_path",
                    "main_business_match",
                    "realization_status",
                    "expected_start",
                    "realization_basis",
                    "uncertainties",
                ],
            },
        },
        "summary": {"type": "string"},
        "unresolved_items": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "event_valid",
        "policy_only",
        "title",
        "event_time",
        "trigger_types",
        "affected_product_or_service",
        "gap_node",
        "candidates",
        "summary",
        "unresolved_items",
    ],
}


def analyze_source_event(
    path: Path,
    *,
    request: HuntingRequestRecord,
    runtime: OllamaStructuredRuntime,
    now: datetime,
    additional_discovery_ids: tuple[str, ...] = (),
    eligible_company_codes: tuple[str, ...] = (),
) -> DeepHuntResult:
    original_discovery_ids = _request_discovery_ids(request)
    discovery_ids = tuple(
        dict.fromkeys(
            (*original_discovery_ids, *additional_discovery_ids)
        )
    )
    items = read_discovery_items(path, discovery_ids)
    if not items:
        raise ValueError("来源事件没有可读取的发现记录")
    as_of_time = request.as_of_time
    _reject_stale_url_dates(items, as_of_time=as_of_time)
    if any(item.published_at > as_of_time for item in items):
        raise ValueError("来源事件包含 as_of_time 之后的记录")
    local_data = LocalIndustryChainData(path)
    universe = local_data.eligible_universe(as_of_time=as_of_time)
    direct_codes = tuple(
        dict.fromkeys(
            [
                *(
                    item.security_code
                    for item in items
                    if item.security_code is not None
                    and item.security_code in universe.codes
                ),
                *(
                    code
                    for code in eligible_company_codes
                    if code in universe.codes
                ),
            ]
        )
    )
    companies = (
        local_data.company_evidence(
            universe_version=universe.universe_version,
            codes=list(direct_codes),
            as_of_time=as_of_time,
        )
        if direct_codes
        else ()
    )
    started_at = datetime.now(now.tzinfo)
    original_id_set = set(original_discovery_ids)
    event_items = tuple(
        item for item in items if item.discovery_id in original_id_set
    )
    industry_items = tuple(
        item
        for item in items
        if item.discovery_id not in original_id_set
        and item.security_code is None
        and not item.payload.get("research_company_profile")
    )[:MAX_EVENT_PROMPT_ITEMS]
    model_result = runtime.complete(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "as_of_time": as_of_time.isoformat(),
                "event_discoveries": [
                    _discovery_prompt_row(item) for item in event_items
                ],
                "industry_research": [
                    _discovery_prompt_row(item) for item in industry_items
                ],
                "eligible_companies": [
                    _company_prompt_row(company) for company in companies
                ],
            },
            ensure_ascii=False,
        ),
        output_schema=DEEP_HUNT_OLLAMA_SCHEMA,
    )
    decision = DeepHuntDecision.model_validate(
        _normalize_deep_hunt_payload(model_result.payload)
    )
    if decision.event_valid and not decision.policy_only and companies:
        focused_candidates: list[CandidateProposal] = []
        focused_notes: list[str] = []
        for offset in range(
            0,
            len(companies),
            MAX_FOCUSED_COMPANIES_PER_BATCH,
        ):
            company_batch = companies[
                offset : offset + MAX_FOCUSED_COMPANIES_PER_BATCH
            ]
            focused_result = runtime.complete(
                system_prompt=FOCUSED_CLOSURE_PROMPT,
                user_prompt=json.dumps(
                    {
                        "as_of_time": as_of_time.isoformat(),
                        "first_pass": decision.model_dump(),
                        "event_discoveries": [
                            _discovery_prompt_row(item)
                            for item in event_items
                        ],
                        "industry_research": [
                            _discovery_prompt_row(item)
                            for item in industry_items
                        ],
                        "company_dossiers": [
                            {
                                "company": _company_prompt_row(company),
                                "discoveries": [
                                    _discovery_prompt_row(item)
                                    for item in _company_dossier_items(
                                        items,
                                        company=company,
                                    )
                                ],
                            }
                            for company in company_batch
                        ],
                    },
                    ensure_ascii=False,
                ),
                output_schema=DEEP_HUNT_OLLAMA_SCHEMA,
            )
            focused_decision = DeepHuntDecision.model_validate(
                _normalize_deep_hunt_payload(focused_result.payload)
            )
            if (
                focused_decision.event_valid
                and not focused_decision.policy_only
            ):
                focused_candidates.extend(focused_decision.candidates)
                focused_notes.extend(focused_decision.unresolved_items)
        merged_candidates: dict[str, CandidateProposal] = {
            candidate.code: candidate
            for candidate in focused_candidates
        }
        for candidate in decision.candidates:
            merged_candidates.setdefault(candidate.code, candidate)
        decision = decision.model_copy(
            update={
                "candidates": list(merged_candidates.values()),
                "unresolved_items": list(
                    dict.fromkeys(
                        [
                            *decision.unresolved_items,
                            *focused_notes,
                        ]
                    )
                ),
            }
        )
    event_time = _event_time(decision, reference=as_of_time)
    if event_time > as_of_time:
        raise ValueError("模型返回的事件时间晚于 as_of_time")
    allowed_codes = {company.code for company in companies}
    outside = sorted(
        {candidate.code for candidate in decision.candidates} - allowed_codes
    )
    if outside:
        decision = decision.model_copy(
            update={
                "candidates": [
                    candidate
                    for candidate in decision.candidates
                    if candidate.code in allowed_codes
                ],
                "unresolved_items": list(
                    dict.fromkeys(
                        [
                            *decision.unresolved_items,
                            f"模型提出名单外公司，已由系统移除：{outside}",
                        ]
                    )
                ),
            }
        )
    if not decision.event_valid or decision.policy_only:
        completed_at = datetime.now(now.tzinfo)
        record_deep_hunt_audit(
            path,
            request_id=request.request_id,
            outcome=(
                "policy_only" if decision.policy_only else "invalid_event"
            ),
            decision=decision.model_dump(),
            error_summary=None,
            model=model_result.model,
            prompt_version=PROMPT_VERSION,
            started_at=started_at,
            completed_at=completed_at,
        )
        return DeepHuntResult(decision, model_result, None, ())

    event_id = _event_id(decision, items, reference=as_of_time)
    repository = IndustryChainRepository(path)
    source_by_id = {source.source_id: source for source in read_latest_sources(path)}
    discovery_evidence = {
        item.discovery_id: _append_discovery_evidence(
            repository,
            item=item,
            event_id=event_id,
            decision=decision,
            source=source_by_id.get(item.source_id),
            as_of_time=as_of_time,
        )
        for item in items
    }
    company_by_code = {company.code: company for company in companies}
    accepted: list[dict[str, object]] = []
    rejected: list[str] = []
    company_evidence_refs: dict[str, str] = {}
    accepted_industry_refs: list[str] = []
    for proposal in decision.candidates:
        company = company_by_code[proposal.code]
        evidence_chain = _candidate_evidence_chain(
            proposal,
            company=company,
            items=items,
            source_by_id=source_by_id,
            as_of_time=as_of_time,
            affected_product=decision.affected_product_or_service,
        )
        reason = _candidate_rejection_reason(
            proposal,
            company=company,
            items=items,
            source_by_id=source_by_id,
            as_of_time=as_of_time,
            affected_product=decision.affected_product_or_service,
            evidence_chain=evidence_chain,
        )
        if reason is not None:
            rejected.append(f"{proposal.code} {proposal.name}：{reason}")
            continue
        company_ref = company_evidence_refs.get(proposal.code)
        web_business_item = _web_business_evidence(
            proposal,
            company=company,
            items=items,
            source_by_id=source_by_id,
            affected_product=decision.affected_product_or_service,
        )
        if (
            company.main_business_name is None
            and web_business_item is None
        ):
            raise RuntimeError("候选通过闸门后缺少主营证据")
        if company_ref is None:
            if company.main_business_name is not None:
                company_ref = _append_local_company_evidence(
                    repository,
                    event_id=event_id,
                    company=company,
                    as_of_time=as_of_time,
                )
            else:
                assert web_business_item is not None
                company_ref = discovery_evidence[
                    web_business_item.discovery_id
                ]
            company_evidence_refs[proposal.code] = company_ref
        realization_refs = tuple(
            discovery_evidence[item.discovery_id]
            for item in evidence_chain.realization_items
        )
        industry_refs = tuple(
            discovery_evidence[item.discovery_id]
            for item in evidence_chain.industry_items
        )
        accepted_industry_refs.extend(industry_refs)
        ownership_item = _ownership_evidence_item(
            proposal,
            company=company,
            items=items,
            source_by_id=source_by_id,
        )
        ownership_ref = (
            discovery_evidence[ownership_item.discovery_id]
            if ownership_item is not None
            else company_ref
        )
        if company.main_business_name is not None:
            business_basis = (
                f"已发布主营快照将“{company.main_business_name}”列为主营"
            )
        else:
            assert web_business_item is not None
            if (
                web_business_item.payload.get("evidence_kind")
                == "reported_business_segments"
            ):
                business_basis = (
                    f"已披露主营构成将“"
                    f"{web_business_item.payload.get('main_business', '')}"
                    "”列为主要产品"
                )
            else:
                business_basis = (
                    f"公司正式披露“{web_business_item.headline}”"
                    "证明相关产品属于实质主营"
                )
        accepted.append(
            _candidate_snapshot(
                proposal,
                company=company,
                rank=len(accepted) + 1,
                industry_refs=industry_refs,
                company_ref=company_ref,
                realization_refs=realization_refs,
                realization_mode=evidence_chain.realization_mode,
                ownership_ref=ownership_ref,
                business_basis=business_basis,
                web_business_item=web_business_item,
            )
    )
    completed_at = datetime.now(now.tzinfo)
    all_discovery_refs = tuple(discovery_evidence.values())
    event_refs = (
        tuple(dict.fromkeys(accepted_industry_refs))
        if accepted
        else all_discovery_refs
    )
    unresolved = tuple(dict.fromkeys([*decision.unresolved_items, *rejected]))
    snapshot = _selection_snapshot(
        request=request,
        decision=decision,
        event_id=event_id,
        universe_version=universe.universe_version,
        model=model_result.model,
        started_at=started_at,
        completed_at=completed_at,
        event_refs=event_refs,
        candidates=accepted,
        unresolved=unresolved,
    )
    publication = repository.publish_selection(
        snapshot,
        source_config_version=SOURCE_CONFIG_VERSION,
    )
    record_deep_hunt_audit(
        path,
        request_id=request.request_id,
        outcome="selected" if accepted else "evidence_insufficient",
        decision=decision.model_dump(),
        error_summary=None,
        model=model_result.model,
        prompt_version=PROMPT_VERSION,
        started_at=started_at,
        completed_at=completed_at,
    )
    return DeepHuntResult(
        decision,
        model_result,
        publication,
        tuple(rejected),
    )


def _request_discovery_ids(request: HuntingRequestRecord) -> tuple[str, ...]:
    if request.trigger_type != "source_event":
        raise ValueError("深挖执行器只处理来源事件")
    payload = json.loads(request.trigger_content)
    values = payload.get("discovery_ids")
    if not isinstance(values, list) or not values:
        raise ValueError("来源事件缺少 discovery_ids")
    return tuple(str(value) for value in values)


def _normalize_deep_hunt_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    normalized = dict(payload)
    notes = _normalized_notes(payload.get("unresolved_items"))
    if payload.get("event_valid") is True:
        trigger_types = payload.get("trigger_types")
        if isinstance(trigger_types, list):
            normalized["trigger_types"] = list(
                dict.fromkeys(
                    value
                    for value in trigger_types
                    if isinstance(value, str)
                )
            )
            trigger_types = normalized["trigger_types"]
        if not isinstance(trigger_types, list) or not trigger_types:
            inferred = _infer_explicit_trigger_type(payload)
            if inferred is None:
                normalized["event_valid"] = False
                normalized["candidates"] = []
                notes.append(
                    "模型将事件标为有效但未给出明确供需触发，"
                    "系统已降为无效事件"
                )
            else:
                normalized["trigger_types"] = [inferred]
                notes.append(
                    f"模型遗漏供需触发类型，系统按明确文本补为 {inferred}"
                )
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        normalized["unresolved_items"] = list(dict.fromkeys(notes))
        return normalized
    accepted: list[object] = []
    if normalized.get("event_valid") is False or payload.get("policy_only") is True:
        raw_candidates = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            notes.append("模型返回了非对象候选，已由系统移除")
            continue
        candidate = dict(raw_candidate)
        raw_code = candidate.get("code")
        if isinstance(raw_code, str):
            code_match = re.search(r"\d{6}", raw_code)
            if code_match is not None:
                candidate["code"] = code_match.group(0)
        path = candidate.get("profit_transmission_path")
        if not isinstance(path, list) or len(path) < 3:
            notes.append(
                f"候选 {candidate.get('code') or '未知代码'} "
                "缺少完整利润传导路径，已由系统移除"
            )
            continue
        expected_start = candidate.get("expected_start")
        if isinstance(expected_start, str) and len(expected_start) > 10:
            match = re.search(r"\d{4}-\d{2}-\d{2}", expected_start)
            if match is not None:
                candidate["expected_start"] = match.group(0)
        accepted.append(candidate)
    normalized["candidates"] = accepted
    normalized["unresolved_items"] = list(
        dict.fromkeys(str(note) for note in notes)
    )
    return normalized


def _normalized_notes(value: object) -> list[str]:
    return [str(note) for note in value] if isinstance(value, list) else []


def _infer_explicit_trigger_type(
    payload: dict[str, object],
) -> TriggerType | None:
    text = " ".join(
        str(payload.get(key) or "")
        for key in (
            "title",
            "summary",
            "affected_product_or_service",
            "gap_node",
        )
    )
    if re.search(r"(停产|暂停生产|减产|限产|供应中断|供应短缺|缺货)", text):
        return "supply_contraction"
    if re.search(r"(重大订单|签订.{0,16}(合同|订单)|中标)", text):
        return "company_order"
    if re.search(r"(批量供货|批量交付|交付订单|订单排产)", text):
        return "order_delivery"
    if re.search(r"(提价|涨价|价格上涨|售价上涨|报价上调)", text):
        return "price_change"
    if re.search(r"(需求增长|需求增加|客户采购|销量增长|供不应求)", text):
        return "demand_increase"
    if re.search(r"(库存下降|库存去化|库存耗尽)", text):
        return "inventory_depletion"
    return None


def _reject_stale_url_dates(
    items: tuple[DiscoveryItem, ...],
    *,
    as_of_time: datetime,
) -> None:
    threshold_date = (as_of_time - MAX_SOURCE_EVENT_AGE).date()
    matches: list[DiscoveryItem] = []
    for item in items:
        match = URL_DATE_PATTERN.search(item.source_url)
        if match is None:
            continue
        try:
            embedded_date = date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            continue
        if embedded_date < threshold_date:
            raise StaleDiscoveryError(
                f"来源网址日期 {embedded_date.isoformat()} 已超出 72 小时补搜窗口"
            )
        if embedded_date > as_of_time.date():
            raise StaleDiscoveryError(
                f"来源网址日期 {embedded_date.isoformat()} 晚于 as_of_time"
            )


def _discovery_prompt_row(item: DiscoveryItem) -> dict[str, object]:
    return {
        "discovery_id": item.discovery_id,
        "source_id": item.source_id,
        "security_code": item.security_code,
        "security_name": item.security_name,
        "headline": item.headline,
        "published_at": item.published_at.isoformat(),
        "publication_time_known": item.payload.get(
            "publication_time_known",
            item.payload.get("published_at_known"),
        ),
        "search_query": item.payload.get("search_query"),
        "search_purpose": item.payload.get("search_purpose"),
        "content_excerpt": _prompt_excerpt(item),
    }


def _prompt_excerpt(item: DiscoveryItem) -> str:
    snippet = item.payload.get("snippet")
    content = item.payload.get("content")
    parts = [
        value.strip()
        for value in (snippet, content)
        if isinstance(value, str) and value.strip()
    ]
    if not parts:
        return item.headline
    return "；".join(parts)[:600]


def _company_dossier_items(
    items: tuple[DiscoveryItem, ...],
    *,
    company: LocalCompanyEvidence,
) -> tuple[DiscoveryItem, ...]:
    linked = tuple(
        item for item in items if _item_is_company_linked(item, company)
    )
    selected: list[DiscoveryItem] = []
    for predicate in (
        _has_positive_realization_evidence,
        _has_commercial_readiness_evidence,
        lambda item: item.payload.get("evidence_kind")
        == "reported_business_segments",
        lambda item: item.payload.get("evidence_kind")
        == "standing_company_profile",
        lambda item: bool(item.payload.get("search_purpose")),
    ):
        match = next(
            (
                item
                for item in linked
                if item not in selected and predicate(item)
            ),
            None,
        )
        if match is not None:
            selected.append(match)
        if len(selected) >= MAX_COMPANY_DOSSIER_ITEMS:
            break
    for item in linked:
        if item not in selected:
            selected.append(item)
        if len(selected) >= MAX_COMPANY_DOSSIER_ITEMS:
            break
    return tuple(selected)


def _company_prompt_row(company: LocalCompanyEvidence) -> dict[str, object]:
    return {
        "code": company.code,
        "name": company.name,
        "availability": company.availability,
        "main_business_name": company.main_business_name,
        "gross_profit_share": company.gross_profit_share,
        "revenue_share": company.revenue_share,
        "ownership_evidence_available": company.ownership_evidence_available,
        "limitations": company.limitations,
        "official_web_evidence_may_supplement": (
            company.main_business_name is None
        ),
    }


def _event_id(
    decision: DeepHuntDecision,
    items: tuple[DiscoveryItem, ...],
    *,
    reference: datetime,
) -> str:
    fingerprint = json.dumps(
        {
            "title": decision.title,
            "event_time": _event_time(
                decision,
                reference=reference,
            ).isoformat(),
            "product": decision.affected_product_or_service,
            "discoveries": sorted(item.discovery_id for item in items),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"event-{hashlib.sha256(fingerprint.encode()).hexdigest()[:32]}"


def _append_discovery_evidence(
    repository: IndustryChainRepository,
    *,
    item: DiscoveryItem,
    event_id: str,
    decision: DeepHuntDecision,
    source: RegisteredSource | None,
    as_of_time: datetime,
) -> str:
    tier = source.source_tier if source is not None else 5
    source_type = _evidence_source_type(source)
    excerpt = _item_excerpt(item)
    evidence_id = f"evidence-{hashlib.sha256(f'{event_id}|{item.discovery_id}'.encode()).hexdigest()[:32]}"
    result = repository.append_evidence(
        {
            "schema_version": "industry-chain-evidence-v0.1",
            "evidence_id": evidence_id,
            "event_id": event_id,
            "source": {
                "source_id": item.source_id,
                "source_name": source.source_name if source is not None else item.source_id,
                "source_tier": tier,
                "source_type": source_type,
                "url": item.source_url,
                "is_primary": tier <= 2,
                "access_class": "public_no_login",
            },
            "headline": item.headline,
            "published_at": item.published_at.isoformat(),
            "updated_at": None,
            "collected_at": item.collected_at.isoformat(),
            "content_sha256": item.content_sha256,
            "language": "zh-CN",
            "is_reprint": False,
            "reprint_cluster_id": None,
            "retention_mode": (
                "official_original"
                if tier <= 2
                else "model_selected_excerpts"
                if tier == 3
                else "metadata_only"
            ),
            "original_file_path": None,
            "facts": [
                {
                    "fact_id": f"fact-{item.discovery_id}",
                    "fact_type": _fact_type(decision.trigger_types[0]),
                    "subject": decision.affected_product_or_service,
                    "statement": decision.summary,
                    "disclosure_mode": (
                        "official_qualitative"
                        if tier <= 2
                        else "media_reported"
                        if tier == 3
                        else "unverified_lead"
                    ),
                    "numeric_value": None,
                    "unit": None,
                    "period": None,
                    "excerpt": excerpt[:1500] or item.headline,
                    "retention_reason": "支撑本次供需事件判断",
                }
            ],
        },
        as_of_time=as_of_time,
    )
    return result.evidence_id


def _append_local_company_evidence(
    repository: IndustryChainRepository,
    *,
    event_id: str,
    company: LocalCompanyEvidence,
    as_of_time: datetime,
) -> str:
    if company.published_at is None or not company.source_urls:
        raise ValueError("本地主营证据缺少发布时间或来源网址")
    payload = {
        "code": company.code,
        "name": company.name,
        "main_business_name": company.main_business_name,
        "gross_profit_share": company.gross_profit_share,
        "revenue_share": company.revenue_share,
        "published_at": company.published_at.isoformat(),
        "source_urls": company.source_urls,
    }
    content_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    evidence_id = f"evidence-company-{event_id[-12:]}-{company.code}"
    statement = f"{company.name}已发布主营业务：{company.main_business_name}"
    if company.gross_profit_share is not None:
        statement += f"，毛利润占比 {company.gross_profit_share:.2%}"
    return repository.append_evidence(
        {
            "schema_version": "industry-chain-evidence-v0.1",
            "evidence_id": evidence_id,
            "event_id": event_id,
            "source": {
                "source_id": "local-fundamental",
                "source_name": "本地已发布主营快照",
                "source_tier": 2,
                "source_type": "local_database",
                "url": company.source_urls[0],
                "is_primary": False,
                "access_class": "public_no_login",
            },
            "headline": f"{company.code} {company.name} 主营构成",
            "published_at": company.published_at.isoformat(),
            "updated_at": None,
            "collected_at": max(company.published_at, as_of_time).isoformat(),
            "content_sha256": content_sha256,
            "language": "zh-CN",
            "is_reprint": False,
            "reprint_cluster_id": None,
            "retention_mode": "model_selected_excerpts",
            "original_file_path": None,
            "facts": [
                {
                    "fact_id": f"fact-business-{company.code}",
                    "fact_type": "business_materiality",
                    "subject": company.name,
                    "statement": statement,
                    "disclosure_mode": (
                        "quantitative"
                        if company.gross_profit_share is not None
                        else "official_qualitative"
                    ),
                    "numeric_value": company.gross_profit_share,
                    "unit": "share" if company.gross_profit_share is not None else None,
                    "period": (
                        company.evidence_as_of_date.isoformat()
                        if company.evidence_as_of_date is not None
                        else None
                    ),
                    "excerpt": statement,
                    "retention_reason": "证明相关产品属于实质主营",
                }
            ],
        },
        as_of_time=as_of_time,
    ).evidence_id


def _candidate_evidence_chain(
    proposal: CandidateProposal,
    *,
    company: LocalCompanyEvidence,
    items: tuple[DiscoveryItem, ...],
    source_by_id: dict[str, RegisteredSource],
    as_of_time: datetime,
    affected_product: str,
) -> CandidateEvidenceChain:
    product_terms = _affected_product_terms(affected_product)
    industry_items = tuple(
        item
        for item in items
        if _is_formal_timed_item(item, source_by_id)
        and not item.payload.get("research_company_profile")
        and item.payload.get("evidence_kind")
        not in {"standing_company_profile", "reported_business_segments"}
        and _is_within_evidence_window(
            item,
            as_of_time=as_of_time,
            maximum_age=MAX_SOURCE_EVENT_AGE,
        )
        and _item_matches_product(item, product_terms)
        and _has_supply_demand_evidence(item)
    )
    company_product_items = tuple(
        item
        for item in items
        if _item_is_benefit_linked(item, company, proposal)
        and _is_formal_timed_item(item, source_by_id)
        and _is_within_evidence_window(
            item,
            as_of_time=as_of_time,
            maximum_age=MAX_COMPANY_REALIZATION_EVIDENCE_AGE,
        )
        and _item_matches_product(item, product_terms)
    )
    direct_items = tuple(
        item
        for item in company_product_items
        if _has_positive_realization_evidence(item)
    )
    stitched_items = tuple(
        item
        for item in company_product_items
        if _has_commercial_readiness_evidence(item)
    )
    if direct_items:
        realization_mode: Literal["direct", "stitched"] | None = "direct"
        realization_items = tuple(
            {
                item.discovery_id: item
                for item in (*direct_items, *stitched_items)
            }.values()
        )
    elif industry_items and stitched_items:
        realization_mode = "stitched"
        realization_items = stitched_items
    else:
        realization_mode = None
        realization_items = ()
    latest_realization_time = max(
        (item.published_at for item in realization_items),
        default=None,
    )
    counter_items = tuple(
        item
        for item in company_product_items
        if _has_formal_counter_evidence(item)
        and (
            latest_realization_time is None
            or item.published_at >= latest_realization_time
        )
    )
    return CandidateEvidenceChain(
        industry_items=industry_items,
        company_product_items=company_product_items,
        realization_items=realization_items,
        counter_items=counter_items,
        realization_mode=realization_mode,
    )


def _candidate_rejection_reason(
    proposal: CandidateProposal,
    *,
    company: LocalCompanyEvidence,
    items: tuple[DiscoveryItem, ...],
    source_by_id: dict[str, RegisteredSource],
    as_of_time: datetime,
    affected_product: str,
    evidence_chain: CandidateEvidenceChain,
) -> str | None:
    if not proposal.main_business_match:
        return "模型未确认相关产品属于主营"
    if (
        company.main_business_name is None
        and (
            web_business_item := _web_business_evidence(
            proposal,
            company=company,
            items=items,
            source_by_id=source_by_id,
            affected_product=affected_product,
        )
        ) is None
    ):
        return "该时间点之前没有可用主营证据"
    if (
        company.gross_profit_share is not None
        and company.gross_profit_share < 0.3
    ):
        return "已披露主营毛利润占比未达到 30%"
    if (
        company.main_business_name is None
        and web_business_item is not None
        and (web_gross_profit_share := _payload_float(
            web_business_item.payload.get("gross_profit_share")
        ))
        is not None
        and web_gross_profit_share < 0.3
    ):
        return "已披露主营毛利润占比未达到 30%"
    if (
        proposal.expected_start
        and date.fromisoformat(proposal.expected_start)
        > as_of_time.date() + timedelta(days=92)
    ):
        return "预计兑现时间超过一个季度"
    if not evidence_chain.industry_items:
        return "缺少产品一致且处于当前窗口的正式行业供需证据"
    if not evidence_chain.company_product_items:
        return "缺少主体、产品和时间一致的公司正式证据"
    if evidence_chain.counter_items:
        return "存在更新或同期正式反证，无法确认公司兑现"
    if not evidence_chain.realization_items:
        return "缺少与该公司直接关联的需求或销售兑现证据"
    if proposal.relation_type != "listed_company_direct":
        if not proposal.related_entity.strip():
            return "间接受益缺少子公司或参股公司名称"
        if (
            proposal.relation_type == "consolidated_subsidiary"
            and proposal.holding_share is not None
            and proposal.holding_share < 0.5
        ):
            return "并表子公司持股比例低于50%"
        if proposal.relation_type == "equity_method_investee":
            disclosed = tuple(
                share
                for share in (
                    proposal.holding_share,
                    proposal.attributable_profit_share,
                )
                if share is not None
            )
            if any(share < 0.2 for share in disclosed):
                return "参股关系持股或利润贡献低于20%"
        if (
            _ownership_evidence_item(
                proposal,
                company=company,
                items=items,
                source_by_id=source_by_id,
            )
            is None
        ):
            return "缺少上市公司与受益主体之间的正式持股或并表证据"
    if any(
        NEGATED_EVIDENCE.search(step)
        for step in proposal.profit_transmission_path
    ):
        return "利润传导包含假设或未确认环节"
    return None


def _has_positive_realization_evidence(item: DiscoveryItem) -> bool:
    text = _item_excerpt(item)
    return _has_non_negated_pattern(text, POSITIVE_REALIZATION_PATTERNS)


def _has_supply_demand_evidence(item: DiscoveryItem) -> bool:
    text = _item_excerpt(item)
    return _has_non_negated_pattern(
        text,
        (*SUPPLY_DEMAND_EVIDENCE_PATTERNS, *POSITIVE_REALIZATION_PATTERNS),
    )


def _has_commercial_readiness_evidence(item: DiscoveryItem) -> bool:
    text = _item_excerpt(item)
    return _has_non_negated_pattern(text, COMMERCIAL_READINESS_PATTERNS)


def _has_formal_counter_evidence(item: DiscoveryItem) -> bool:
    text = _item_excerpt(item)
    return any(
        pattern.search(text)
        for pattern in FORMAL_COUNTER_EVIDENCE_PATTERNS
    )


def _has_non_negated_pattern(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            window = text[
                max(0, match.start() - 18) : min(
                    len(text),
                    match.end() + 12,
                )
            ]
            if NEGATED_EVIDENCE.search(window) is None:
                return True
    return False


def _is_within_evidence_window(
    item: DiscoveryItem,
    *,
    as_of_time: datetime,
    maximum_age: timedelta,
) -> bool:
    return as_of_time - maximum_age <= item.published_at <= as_of_time


def _affected_product_terms(affected_product: str) -> tuple[str, ...]:
    return tuple(
        value
        for value in re.split(
            r"[\s,，。；;、/\\()（）\-]+",
            affected_product,
        )
        if len(value) >= 2
    )


def _item_matches_product(
    item: DiscoveryItem,
    product_terms: tuple[str, ...],
) -> bool:
    if not product_terms:
        return False
    text = " ".join((item.headline, _item_excerpt(item)))
    if item.security_name:
        text = text.replace(item.security_name, " ")
    return _product_evidence_matches(
        product_terms,
        text,
    )


def _web_business_evidence(
    proposal: CandidateProposal,
    *,
    company: LocalCompanyEvidence,
    items: tuple[DiscoveryItem, ...],
    source_by_id: dict[str, RegisteredSource],
    affected_product: str,
) -> DiscoveryItem | None:
    product_terms = _affected_product_terms(affected_product)
    matches: list[DiscoveryItem] = []
    for item in items:
        source = source_by_id.get(item.source_id)
        if (
            not _item_is_company_linked(item, company)
            or source is None
            or (
                source.source_tier > 2
                and item.payload.get("evidence_kind")
                != "reported_business_segments"
            )
            or (
                not _is_formal_timed_item(item, source_by_id)
                and item.payload.get("evidence_kind")
                not in {
                    "standing_company_profile",
                    "reported_business_segments",
                }
            )
        ):
            continue
        text = " ".join((item.headline, _item_excerpt(item)))
        if company.name not in text:
            continue
        if not re.search(r"(主营业务|主营构成|主要从事|收入占比|毛利润)", text):
            continue
        if product_terms and not _product_evidence_matches(product_terms, text):
            continue
        matches.append(item)
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            item.payload.get("evidence_kind")
            != "reported_business_segments",
            source_by_id[item.source_id].source_tier,
            item.discovery_id,
        ),
    )


def _item_is_company_linked(
    item: DiscoveryItem,
    company: LocalCompanyEvidence,
) -> bool:
    if item.security_code == company.code:
        return True
    return company_name_mentioned(
        company.name,
        " ".join((item.headline, _item_excerpt(item))),
    )


def _item_is_benefit_linked(
    item: DiscoveryItem,
    company: LocalCompanyEvidence,
    proposal: CandidateProposal,
) -> bool:
    if _item_is_company_linked(item, company):
        return True
    related_entity = proposal.related_entity.strip()
    return bool(
        related_entity
        and related_entity in " ".join((item.headline, _item_excerpt(item)))
    )


def _ownership_evidence_item(
    proposal: CandidateProposal,
    *,
    company: LocalCompanyEvidence,
    items: tuple[DiscoveryItem, ...],
    source_by_id: dict[str, RegisteredSource],
) -> DiscoveryItem | None:
    if proposal.relation_type == "listed_company_direct":
        return None
    related_entity = proposal.related_entity.strip()
    if not related_entity:
        return None
    relationship_pattern = re.compile(
        r"(控股子公司|全资子公司|持股|股权|并表|实际控制)"
    )
    for item in items:
        if not _is_formal_timed_item(item, source_by_id):
            continue
        text = " ".join((item.headline, _item_excerpt(item)))
        if (
            company.name in text
            and related_entity in text
            and relationship_pattern.search(text)
        ):
            return item
    return None


def _product_evidence_matches(
    product_terms: tuple[str, ...],
    text: str,
) -> bool:
    if any(term in text for term in product_terms):
        return True
    meaningful_bigrams = {
        term[index : index + 2]
        for term in product_terms
        for index in range(len(term) - 1)
        if term[index : index + 2]
        not in {"产品", "材料", "服务", "相关", "业务", "公司"}
    }
    return bool(meaningful_bigrams.intersection(
        text[index : index + 2] for index in range(len(text) - 1)
    ))


def _payload_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _is_formal_timed_item(
    item: DiscoveryItem,
    source_by_id: dict[str, RegisteredSource],
) -> bool:
    source = source_by_id.get(item.source_id)
    if source is None or source.source_tier > 3:
        return False
    publication_time_known = item.payload.get("publication_time_known")
    if publication_time_known is None:
        publication_time_known = item.payload.get("published_at_known")
    return publication_time_known is True


def _candidate_snapshot(
    proposal: CandidateProposal,
    *,
    company: LocalCompanyEvidence,
    rank: int,
    industry_refs: tuple[str, ...],
    company_ref: str,
    realization_refs: tuple[str, ...],
    realization_mode: Literal["direct", "stitched"] | None,
    ownership_ref: str,
    business_basis: str,
    web_business_item: DiscoveryItem | None,
) -> dict[str, object]:
    web_revenue_share = (
        _payload_float(web_business_item.payload.get("revenue_share"))
        if web_business_item is not None
        else None
    )
    web_gross_profit_share = (
        _payload_float(
            web_business_item.payload.get("gross_profit_share")
        )
        if web_business_item is not None
        else None
    )
    disclosure_mode = (
        "mixed"
        if (
            company.gross_profit_share is not None
            or web_gross_profit_share is not None
        )
        else "official_qualitative"
    )
    return {
        "rank": rank,
        "code": proposal.code,
        "name": proposal.name,
        "chain_node": proposal.chain_node,
        "benefit_type": proposal.benefit_type,
        "profit_transmission_path": proposal.profit_transmission_path,
        "business_materiality": {
            "main_business_confirmed": True,
            "disclosure_mode": disclosure_mode,
            "revenue_share": (
                company.revenue_share
                if company.revenue_share is not None
                else web_revenue_share
            ),
            "gross_profit_share": (
                company.gross_profit_share
                if company.gross_profit_share is not None
                else web_gross_profit_share
            ),
            "official_qualitative_basis": business_basis,
            "evidence_refs": [company_ref],
        },
        "ownership_relation": {
            "relation_type": proposal.relation_type,
            "holding_share": proposal.holding_share,
            "attributable_profit_share": (
                proposal.attributable_profit_share
            ),
            "official_qualitative_basis": (
                proposal.ownership_basis
                or "本次候选按上市公司直接经营口径核验"
            ),
            "evidence_refs": [ownership_ref],
        },
        "realization": {
            "status": proposal.realization_status,
            "expected_start": proposal.expected_start or None,
            "basis": (
                f"多正式来源拼接：{proposal.realization_basis}"
                if realization_mode == "stitched"
                else proposal.realization_basis
            ),
            "evidence_refs": list(realization_refs),
        },
        "industry_evidence_refs": list(industry_refs),
        "company_evidence_refs": list(
            dict.fromkeys(
                (
                    company_ref,
                    ownership_ref,
                    *realization_refs,
                )
            )
        ),
        "uncertainties": proposal.uncertainties,
    }


def _selection_snapshot(
    *,
    request: HuntingRequestRecord,
    decision: DeepHuntDecision,
    event_id: str,
    universe_version: str,
    model: str,
    started_at: datetime,
    completed_at: datetime,
    event_refs: tuple[str, ...],
    candidates: list[dict[str, object]],
    unresolved: tuple[str, ...],
) -> dict[str, object]:
    selection_id = f"ics-{request.request_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "selection_id": selection_id,
        "selection_version": 1,
        "event_id": event_id,
        "rules_version": RULES_VERSION,
        "model": {
            "model_id": model,
            "model_revision": "local-installed",
            "quantization": "Q4_K_M",
            "runtime_id": "ollama-0.32.3",
            "temperature": 0,
        },
        "prompt_version": PROMPT_VERSION,
        "knowledge_version": KNOWLEDGE_VERSION,
        "stock_universe_version": universe_version,
        "trigger_method": "new_evidence",
        "as_of_time": request.as_of_time.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": "selected" if candidates else "evidence_insufficient",
        "decision_authority": "candidate_selection_only",
        "immutable": True,
        "contains_trading_instruction": False,
        "event": {
            "title": decision.title,
            "event_time": _event_time(
                decision,
                reference=request.as_of_time,
            ).isoformat(),
            "trigger_types": decision.trigger_types,
            "affected_product_or_service": decision.affected_product_or_service,
            "gap_node": decision.gap_node,
            "industry_evidence_refs": list(event_refs),
            "policy_only": False,
        },
        "candidates": candidates,
        "summary": decision.summary,
        "unresolved_items": list(unresolved),
    }


def _item_excerpt(item: DiscoveryItem) -> str:
    for key in ("content", "brief", "snippet", "headline"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return item.headline


def _evidence_source_type(source: RegisteredSource | None) -> str:
    if source is None:
        return "other"
    if source.source_type == "exchange_disclosure":
        return "exchange"
    if source.source_type == "financial_media":
        return "financial_media"
    if source.source_type == "social_discovery":
        return "social"
    if source.source_type == "government_statistics":
        return "statistics"
    if source.source_type == "government":
        return "government"
    return "other"


def _fact_type(trigger_type: TriggerType) -> str:
    return {
        "supply_contraction": "supply_contraction",
        "demand_increase": "demand_increase",
        "inventory_depletion": "inventory_depletion",
        "order_delivery": "order_delivery",
        "price_change": "price_change",
        "company_order": "order_delivery",
        "other_supply_demand": "other_supply_demand",
    }[trigger_type]


def _event_time(
    decision: DeepHuntDecision,
    *,
    reference: datetime,
) -> datetime:
    value = datetime.fromisoformat(decision.event_time.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("参考时间必须包含时区")
        value = value.replace(tzinfo=reference.tzinfo)
    return value
