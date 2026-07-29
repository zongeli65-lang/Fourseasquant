from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


OpinionPlatform = Literal["eastmoney", "sina", "tonghuashun", "xueqiu"]
OpinionContentKind = Literal["topic", "reply"]
OpinionSourceType = Literal[
    "formal",
    "media",
    "user_repost",
    "user_original",
    "platform",
]
OpinionSentiment = Literal["favorable", "unfavorable", "disputed", "unknown"]
ModelOpinionLabel = Literal[
    "favorable",
    "unfavorable",
    "disputed",
    "neutral",
    "unrelated",
]
OpinionDirection = Literal["favorable", "unfavorable", "balanced"]
DirectionStatus = Literal["collecting", "insufficient_sample", "published"]

BEIJING = ZoneInfo("Asia/Shanghai")
FAVORABLE_TERMS = frozenset(
    {
        "利好",
        "盈利",
        "增长",
        "增持",
        "回购",
        "中标",
        "扭亏",
        "突破",
    }
)
UNFAVORABLE_TERMS = frozenset(
    {
        "利空",
        "亏损",
        "减持",
        "处罚",
        "退市",
        "下跌",
        "违约",
        "暴雷",
    }
)
NEGATIONS = frozenset({"不", "未", "无", "没有", "并非", "不是"})


class OpinionContent(BaseModel):
    platform: OpinionPlatform
    content_id: str = Field(min_length=1)
    code: str = Field(pattern=r"^\d{6}$")
    url: str = Field(min_length=1)
    published_at: datetime
    collected_at: datetime
    likes: int | None = Field(default=None, ge=0)
    content_kind: OpinionContentKind
    source_type: OpinionSourceType
    text: str


class ClassifiedOpinionContent(BaseModel):
    sentiment: OpinionSentiment
    favorable_terms: list[str]
    unfavorable_terms: list[str]
    weight: float


class ModelOpinionClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: str = Field(min_length=1)
    label: ModelOpinionLabel
    confidence: float = Field(ge=0, le=1)
    model: str
    prompt_version: str


class PlatformOpinionAggregate(BaseModel):
    platform: OpinionPlatform
    actual_date: date
    code: str
    collection_complete: bool
    content_count: int
    valid_count: int
    favorable_count: int
    unfavorable_count: int
    disputed_count: int
    unknown_count: int
    classified_count: int = 0
    neutral_count: int = 0
    unrelated_count: int = 0
    low_confidence_count: int = 0
    weighted_favorable: float
    weighted_unfavorable: float
    weighted_disputed: float
    weighted_neutral: float = 0
    direction_index: float | None
    direction: OpinionDirection | None
    direction_status: DirectionStatus
    likes_missing: bool
    rules_version: str
    sample_capped: bool = False
    sample_limit: int | None = Field(default=None, ge=1)


def classify_public_opinion(text: str, likes: int | None) -> ClassifiedOpinionContent:
    favorable = sorted(_active_terms(text, FAVORABLE_TERMS))
    unfavorable = sorted(_active_terms(text, UNFAVORABLE_TERMS))
    if favorable and unfavorable:
        sentiment: OpinionSentiment = "disputed"
    elif favorable:
        sentiment = "favorable"
    elif unfavorable:
        sentiment = "unfavorable"
    else:
        sentiment = "unknown"
    weight = 1.0 if likes is None else 1 + math.log(1 + likes)
    return ClassifiedOpinionContent(
        sentiment=sentiment,
        favorable_terms=favorable,
        unfavorable_terms=unfavorable,
        weight=weight,
    )


def aggregate_public_opinion(
    contents: list[OpinionContent],
    *,
    classifications: Mapping[str, ModelOpinionClassification] | None = None,
    actual_date: date,
    platform: OpinionPlatform,
    code: str,
    collection_complete: bool,
    rules_version: str,
    sample_capped: bool = False,
    sample_limit: int | None = None,
) -> PlatformOpinionAggregate:
    if sample_capped and sample_limit is None:
        raise ValueError("截断舆论样本必须注明采集上限")
    unique: dict[str, OpinionContent] = {}
    for content in contents:
        if content.platform != platform or content.code != code:
            raise ValueError("单次舆论汇总只能包含同一股票和平台")
        if content.published_at.astimezone(BEIJING).date() != actual_date:
            raise ValueError("评论必须按北京时间原始发布日期分日汇总")
        unique.setdefault(content.content_id, content)

    favorable_count = 0
    unfavorable_count = 0
    disputed_count = 0
    unknown_count = 0
    weighted_favorable = 0.0
    weighted_unfavorable = 0.0
    weighted_disputed = 0.0
    weighted_neutral = 0.0
    neutral_count = 0
    unrelated_count = 0
    low_confidence_count = 0
    likes_missing = False
    if classifications is not None and set(classifications) != set(unique):
        raise ValueError("大模型分类结果与舆论内容编号不一致")
    for content in unique.values():
        likes_missing = likes_missing or content.likes is None
        if classifications is None:
            mechanical = classify_public_opinion(content.text, content.likes)
            label: ModelOpinionLabel | Literal["unknown"] = mechanical.sentiment
            weight = mechanical.weight
            confidence = 1.0
        else:
            model_result = classifications[content.content_id]
            label = model_result.label
            confidence = model_result.confidence
            weight = (
                1.0
                if content.likes is None
                else 1 + math.log(1 + content.likes)
            )
        if confidence < 0.7:
            low_confidence_count += 1
        if label == "favorable":
            favorable_count += 1
            weighted_favorable += weight
        elif label == "unfavorable":
            unfavorable_count += 1
            weighted_unfavorable += weight
        elif label == "disputed":
            disputed_count += 1
            weighted_disputed += weight
        elif label == "neutral":
            neutral_count += 1
            weighted_neutral += weight
        elif label == "unrelated":
            unrelated_count += 1
        else:
            unknown_count += 1

    valid_count = (
        favorable_count
        + unfavorable_count
        + disputed_count
        + neutral_count
    )
    denominator = (
        weighted_favorable
        + weighted_unfavorable
        + weighted_disputed
        + weighted_neutral
    )
    direction_index = (
        (weighted_favorable - weighted_unfavorable) / denominator
        if denominator > 0
        else None
    )
    if not collection_complete:
        direction_status: DirectionStatus = "collecting"
        direction: OpinionDirection | None = None
    elif valid_count < 10:
        direction_status = "insufficient_sample"
        direction = None
    else:
        direction_status = "published"
        assert direction_index is not None
        if direction_index >= 0.2:
            direction = "favorable"
        elif direction_index <= -0.2:
            direction = "unfavorable"
        else:
            direction = "balanced"

    return PlatformOpinionAggregate(
        platform=platform,
        actual_date=actual_date,
        code=code,
        collection_complete=collection_complete,
        content_count=len(unique),
        valid_count=valid_count,
        favorable_count=favorable_count,
        unfavorable_count=unfavorable_count,
        disputed_count=disputed_count,
        unknown_count=unknown_count,
        classified_count=len(classifications or {}),
        neutral_count=neutral_count,
        unrelated_count=unrelated_count,
        low_confidence_count=low_confidence_count,
        weighted_favorable=weighted_favorable,
        weighted_unfavorable=weighted_unfavorable,
        weighted_disputed=weighted_disputed,
        weighted_neutral=weighted_neutral,
        direction_index=direction_index,
        direction=direction,
        direction_status=direction_status,
        likes_missing=likes_missing,
        rules_version=rules_version,
        sample_capped=sample_capped,
        sample_limit=sample_limit,
    )


def _active_terms(text: str, terms: frozenset[str]) -> set[str]:
    return {
        term
        for term in terms
        if any(
            not _is_negated(text, index)
            for index in _term_positions(text, term)
        )
    }


def _term_positions(text: str, term: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return positions
        positions.append(index)
        start = index + len(term)


def _is_negated(text: str, term_index: int) -> bool:
    prefix = text[max(0, term_index - 4) : term_index]
    return any(prefix.endswith(negation) for negation in NEGATIONS)
