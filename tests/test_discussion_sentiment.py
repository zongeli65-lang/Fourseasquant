from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.discussion_sentiment import (
    DiscussionPost,
    aggregate_platform_discussion,
    combine_platform_aggregates,
)


def test_discussion_heat_keeps_repeated_content_but_not_crawler_duplicates() -> None:
    published_at = datetime(
        2026,
        7,
        24,
        10,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    posts = [
        DiscussionPost(
            platform="eastmoney_guba",
            post_id="1",
            code="600000",
            url="https://example.test/1",
            published_at=published_at,
            likes=0,
            text="重大利好",
        ),
        DiscussionPost(
            platform="eastmoney_guba",
            post_id="2",
            code="600000",
            url="https://example.test/2",
            published_at=published_at,
            likes=3,
            text="重大利好",
        ),
        DiscussionPost(
            platform="eastmoney_guba",
            post_id="2",
            code="600000",
            url="https://example.test/2",
            published_at=published_at,
            likes=3,
            text="重大利好",
        ),
        DiscussionPost(
            platform="eastmoney_guba",
            post_id="3",
            code="600000",
            url="https://example.test/3",
            published_at=published_at,
            likes=8,
            text="股东减持，明显利空",
        ),
        DiscussionPost(
            platform="eastmoney_guba",
            post_id="4",
            code="600000",
            url="https://example.test/4",
            published_at=published_at,
            likes=0,
            text="大家怎么看",
        ),
    ]
    expected_heat = (
        1
        + (1 + math.log(4))
        + (1 + math.log(9))
        + 1
    )

    result = aggregate_platform_discussion(
        posts,
        heat_universe=[1, expected_heat, 20],
    )

    assert result.actual_date == date(2026, 7, 24)
    assert result.post_count == 4
    assert result.positive_count == 2
    assert result.neutral_count == 1
    assert result.negative_count == 1
    assert result.raw_heat == pytest.approx(expected_heat)
    assert result.weighted_sentiment == pytest.approx(
        (
            1
            + (1 + math.log(4))
            - (1 + math.log(9))
        )
        / expected_heat
    )
    assert result.heat_percentile == pytest.approx(200 / 3)


def test_conflicting_or_missing_terms_are_neutral() -> None:
    published_at = datetime(
        2026,
        7,
        24,
        10,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )

    result = aggregate_platform_discussion(
        [
            DiscussionPost(
                platform="xueqiu",
                post_id="1",
                code="600000",
                url="https://example.test/1",
                published_at=published_at,
                likes=None,
                text="增长是利好，但减持也是利空",
            ),
            DiscussionPost(
                platform="xueqiu",
                post_id="2",
                code="600000",
                url="https://example.test/2",
                published_at=published_at,
                likes=None,
                text="今天开会讨论",
            ),
        ],
        heat_universe=[2],
    )

    assert result.post_count == 2
    assert result.neutral_count == 2
    assert result.weighted_sentiment == 0
    assert result.likes_missing is True


def test_eastmoney_and_xueqiu_are_combined_equally_only_when_both_exist() -> None:
    published_at = datetime(
        2026,
        7,
        24,
        10,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    eastmoney = aggregate_platform_discussion(
        [
            DiscussionPost(
                platform="eastmoney_guba",
                post_id="1",
                code="600000",
                url="https://example.test/em",
                published_at=published_at,
                likes=0,
                text="利好",
            )
        ],
        heat_universe=[1, 2],
    )
    xueqiu = aggregate_platform_discussion(
        [
            DiscussionPost(
                platform="xueqiu",
                post_id="1",
                code="600000",
                url="https://example.test/xq",
                published_at=published_at,
                likes=0,
                text="利空",
            )
        ],
        heat_universe=[1],
    )

    combined = combine_platform_aggregates(eastmoney, xueqiu)

    assert combined is not None
    assert combined.heat_percentile == pytest.approx(75)
    assert combined.weighted_sentiment == pytest.approx(0)
    assert combine_platform_aggregates(eastmoney, None) is None
