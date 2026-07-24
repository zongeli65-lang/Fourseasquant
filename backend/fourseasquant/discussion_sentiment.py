from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


DiscussionPlatform = Literal["eastmoney_guba", "xueqiu"]
Sentiment = Literal["positive", "neutral", "negative"]

POSITIVE_TERMS = frozenset(
    {
        "利好",
        "增持",
        "回购",
        "增长",
        "中标",
        "扭亏",
        "盈利",
        "突破",
    }
)
NEGATIVE_TERMS = frozenset(
    {
        "利空",
        "减持",
        "亏损",
        "暴雷",
        "处罚",
        "退市",
        "下跌",
        "违约",
    }
)


class DiscussionPost(BaseModel):
    platform: DiscussionPlatform
    post_id: str = Field(min_length=1)
    code: str = Field(pattern=r"^\d{6}$")
    url: str = Field(min_length=1)
    published_at: datetime
    likes: int | None = Field(default=None, ge=0)
    text: str
    content_type: Literal["user_original"]


class PlatformDiscussionAggregate(BaseModel):
    platform: DiscussionPlatform
    actual_date: date
    code: str
    post_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    raw_heat: float
    weighted_sentiment: float
    heat_percentile: float | None
    likes_missing: bool


class CombinedDiscussionSignal(BaseModel):
    actual_date: date
    code: str
    heat_percentile: float
    weighted_sentiment: float


def classify_sentiment(text: str) -> Sentiment:
    has_positive = any(term in text for term in POSITIVE_TERMS)
    has_negative = any(term in text for term in NEGATIVE_TERMS)
    if has_positive == has_negative:
        return "neutral"
    return "positive" if has_positive else "negative"


def aggregate_platform_discussion(
    posts: list[DiscussionPost],
    *,
    heat_universe: list[float],
) -> PlatformDiscussionAggregate:
    if not posts:
        raise ValueError("讨论帖子为空，无法生成平台汇总")
    unique_posts: list[DiscussionPost] = []
    seen: set[tuple[str, str, str]] = set()
    for post in posts:
        key = (post.platform, post.post_id, post.code)
        if key in seen:
            continue
        seen.add(key)
        unique_posts.append(post)

    first = unique_posts[0]
    actual_date = first.published_at.date()
    if any(
        post.platform != first.platform
        or post.code != first.code
        or post.published_at.date() != actual_date
        for post in unique_posts
    ):
        raise ValueError("单次平台汇总只能包含同一股票、平台和日期")

    positive_count = 0
    neutral_count = 0
    negative_count = 0
    raw_heat = 0.0
    sentiment_total = 0.0
    likes_missing = False
    for post in unique_posts:
        sentiment = classify_sentiment(post.text)
        if sentiment == "positive":
            positive_count += 1
            sentiment_value = 1
        elif sentiment == "negative":
            negative_count += 1
            sentiment_value = -1
        else:
            neutral_count += 1
            sentiment_value = 0
        if post.likes is None:
            likes_missing = True
            weight = 1.0
        else:
            weight = 1 + math.log(1 + post.likes)
        raw_heat += weight
        sentiment_total += sentiment_value * weight

    return PlatformDiscussionAggregate(
        platform=first.platform,
        actual_date=actual_date,
        code=first.code,
        post_count=len(unique_posts),
        positive_count=positive_count,
        neutral_count=neutral_count,
        negative_count=negative_count,
        raw_heat=raw_heat,
        weighted_sentiment=sentiment_total / raw_heat,
        heat_percentile=_percentile(raw_heat, heat_universe),
        likes_missing=likes_missing,
    )


def combine_platform_aggregates(
    eastmoney: PlatformDiscussionAggregate | None,
    xueqiu: PlatformDiscussionAggregate | None,
) -> CombinedDiscussionSignal | None:
    if eastmoney is None or xueqiu is None:
        return None
    if eastmoney.platform != "eastmoney_guba" or xueqiu.platform != "xueqiu":
        raise ValueError("综合舆情要求东方财富股吧和雪球各一份")
    if (
        eastmoney.code != xueqiu.code
        or eastmoney.actual_date != xueqiu.actual_date
    ):
        raise ValueError("两个平台的股票代码和日期必须一致")
    if eastmoney.heat_percentile is None or xueqiu.heat_percentile is None:
        return None
    return CombinedDiscussionSignal(
        actual_date=eastmoney.actual_date,
        code=eastmoney.code,
        heat_percentile=(
            eastmoney.heat_percentile + xueqiu.heat_percentile
        )
        / 2,
        weighted_sentiment=(
            eastmoney.weighted_sentiment + xueqiu.weighted_sentiment
        )
        / 2,
    )


def _percentile(value: float, universe: list[float]) -> float | None:
    valid = [item for item in universe if item >= 0]
    if not valid:
        return None
    return sum(item <= value for item in valid) / len(valid) * 100
