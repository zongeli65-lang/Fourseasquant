from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fourseasquant.industry_chain.discovery import DiscoveryItem
from fourseasquant.industry_chain.ollama_runtime import (
    OllamaStructuredRuntime,
    StructuredModelResult,
)


TriageEventType = Literal[
    "supply_contraction",
    "capacity_expansion",
    "demand_growth",
    "inventory_change",
    "price_change",
    "major_order",
    "policy_watch",
    "unrelated",
    "uncertain",
]


class TriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovery_id: str
    action: Literal["investigate", "observe", "ignore"]
    suspected_event_type: TriageEventType
    priority: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_action_and_event_type(self) -> TriageDecision:
        if self.action == "ignore" and self.suspected_event_type != "unrelated":
            raise ValueError("忽略项只能标记 unrelated")
        if self.action == "observe" and self.suspected_event_type != "policy_watch":
            raise ValueError("观察项只能标记 policy_watch")
        if self.action == "investigate" and self.suspected_event_type in {
            "policy_watch",
            "unrelated",
        }:
            raise ValueError("深挖项不能标记为政策观察或无关")
        return self


class TriageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[TriageDecision]


@dataclass(frozen=True)
class AnnouncementTriageResult:
    batch: TriageBatch
    model_result: StructuredModelResult

    @property
    def investigate_ids(self) -> tuple[str, ...]:
        return tuple(
            decision.discovery_id
            for decision in self.batch.decisions
            if decision.action == "investigate"
        )


SYSTEM_PROMPT = """
你是产业链供需信息初筛 Agent，只判断公告、财经新闻或公开帖子是否值得进一步读取全文。
你不负责最终选股，不使用技术走势、股价、估值或基本面投资分。
只根据标题和已有元数据判断潜在供需事件，不得编造未提供的正文。
重大订单、批量供货、停产限产、扩产投产、客户需求、价格库存与产能变化应进一步调查。
新产线投产、扩产和新增产能标记 capacity_expansion，不能标记 supply_contraction。
股东会、人员任免、分红、质押、减持、一般治理和纯资本运作通常忽略。
只有政策表述时必须使用 observe + policy_watch，不得写成实际供需变化。
使用 ignore 时必须标记 unrelated；使用 investigate 时不得标记 unrelated 或 policy_watch。
每个输入必须且只能输出一个决定。
""".strip()

TRIAGE_OLLAMA_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "discovery_id": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["investigate", "observe", "ignore"],
                    },
                    "suspected_event_type": {
                        "type": "string",
                        "enum": [
                            "supply_contraction",
                            "capacity_expansion",
                            "demand_growth",
                            "inventory_change",
                            "price_change",
                            "major_order",
                            "policy_watch",
                            "unrelated",
                            "uncertain",
                        ],
                    },
                    "priority": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3,
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "discovery_id",
                    "action",
                    "suspected_event_type",
                    "priority",
                    "reason",
                ],
            },
        },
    },
    "required": ["decisions"],
}


def triage_announcements(
    items: tuple[DiscoveryItem, ...],
    *,
    runtime: OllamaStructuredRuntime,
) -> AnnouncementTriageResult:
    if not items:
        raise ValueError("公告初筛批次不能为空")
    if len(items) > 30:
        raise ValueError("单次公告初筛最多 30 条")
    input_rows = [
        {
            "discovery_id": item.discovery_id,
            "security_code": item.security_code,
            "security_name": item.security_name,
            "headline": item.headline,
            "published_at": item.published_at.isoformat(),
            "content_excerpt": _content_excerpt(item),
        }
        for item in items
    ]
    model_result = runtime.complete(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(input_rows, ensure_ascii=False),
        output_schema=TRIAGE_OLLAMA_SCHEMA,
    )
    batch = TriageBatch.model_validate(
        _normalize_decision_consistency(model_result.payload)
    )
    expected = {item.discovery_id for item in items}
    returned = [decision.discovery_id for decision in batch.decisions]
    if len(returned) != len(set(returned)):
        raise ValueError("公告初筛结果包含重复 discovery_id")
    if set(returned) != expected:
        missing = sorted(expected - set(returned))
        unknown = sorted(set(returned) - expected)
        raise ValueError(
            f"公告初筛结果编号不完整：缺少 {missing}，未知 {unknown}"
        )
    ordered = sorted(
        batch.decisions,
        key=lambda decision: next(
            index
            for index, item in enumerate(items)
            if item.discovery_id == decision.discovery_id
        ),
    )
    return AnnouncementTriageResult(
        batch=TriageBatch(decisions=ordered),
        model_result=model_result,
    )


def _content_excerpt(item: DiscoveryItem) -> str:
    for key in ("content", "brief", "text"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:1000]
    return ""


def _normalize_decision_consistency(
    payload: dict[str, object],
) -> dict[str, object]:
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list):
        return payload
    normalized: list[object] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            normalized.append(raw)
            continue
        decision = dict(raw)
        action = decision.get("action")
        event_type = decision.get("suspected_event_type")
        if event_type == "policy_watch":
            decision["action"] = "observe"
        elif event_type == "unrelated":
            decision["action"] = "ignore"
        elif action == "ignore":
            decision["suspected_event_type"] = "unrelated"
        elif action == "observe":
            decision["suspected_event_type"] = "policy_watch"
        normalized.append(decision)
    return {**payload, "decisions": normalized}
