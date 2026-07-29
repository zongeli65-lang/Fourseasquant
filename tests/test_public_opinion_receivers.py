from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fourseasquant.public_opinion_receivers import (
    PublicSourceGuard,
    SourcePausedError,
    parse_eastmoney_detail,
    parse_eastmoney_hot_payload,
    parse_eastmoney_list,
    parse_eastmoney_reply_payload,
    parse_sina_bar_config,
    parse_sina_reply_payload,
    parse_sina_topic_payload,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_eastmoney_public_html_parsers_extract_reference_and_original_time() -> None:
    listing = """
    <table><tbody>
      <tr class="listitem">
        <td><div class="read">90</div></td>
        <td><div class="reply">1</div></td>
        <td><div class="title"><a data-postid="1749609162"
          data-posttype="0" href="/news,000001,1749609162.html">
          作为小股东，支持平安银行
        </a></div></td>
        <td><div class="author"><a>漫步时光016</a></div></td>
        <td><div class="update">07-26 07:34</div></td>
      </tr>
    </tbody></table>
    """
    details = r"""
    <script>
    var article = {"post_id":"1749609162","post_type":0,
      "post_title":"作为小股东，支持平安银行",
      "post_content":"<p>盈利增长，明显利好。</p>",
      "post_publish_time":"2026-07-26 19:34:11",
      "post_like_count":12,"post_comment_count":1};
    </script>
    """

    references = parse_eastmoney_list(listing, code="000001")
    content = parse_eastmoney_detail(
        details,
        code="000001",
        collected_at=datetime(2026, 7, 26, 20, 0, tzinfo=BEIJING),
    )

    assert len(references) == 1
    assert references[0].content_id == "1749609162"
    assert references[0].reply_count == 1
    assert references[0].url == (
        "https://guba.eastmoney.com/news,000001,1749609162.html"
    )
    assert content.content_id == "1749609162"
    assert content.published_at == datetime(
        2026, 7, 26, 19, 34, 11, tzinfo=BEIJING
    )
    assert content.likes == 12
    assert content.text == "作为小股东，支持平安银行 盈利增长，明显利好。"
    assert content.source_type == "user_original"


def test_public_source_guard_enforces_interval_pause_and_circuit_breaker() -> None:
    start = datetime(2026, 7, 26, 10, 0, tzinfo=BEIJING)
    guard = PublicSourceGuard(minimum_interval_seconds=5.0)

    assert guard.delay_before_request(start) == 0
    guard.record_success(start)
    assert guard.delay_before_request(start + timedelta(seconds=2)) == 3

    guard.record_http_status(429, start + timedelta(seconds=5))
    with pytest.raises(SourcePausedError, match="429"):
        guard.delay_before_request(start + timedelta(minutes=10))
    assert guard.delay_before_request(start + timedelta(minutes=31)) == 0

    guard.record_http_status(500, start + timedelta(minutes=31))
    guard.record_http_status(502, start + timedelta(minutes=32))
    guard.record_http_status(503, start + timedelta(minutes=33))
    with pytest.raises(SourcePausedError, match="连续失败"):
        guard.delay_before_request(start + timedelta(minutes=34))

    daily_guard = PublicSourceGuard(minimum_interval_seconds=5.0)
    daily_guard.record_http_status(403, start)
    with pytest.raises(SourcePausedError, match="当日"):
        daily_guard.delay_before_request(start + timedelta(hours=8))
    assert daily_guard.delay_before_request(
        datetime(2026, 7, 27, 0, 0, tzinfo=BEIJING)
    ) == 0


def test_eastmoney_hot_payload_requires_complete_ranked_list() -> None:
    stocks = parse_eastmoney_hot_payload(
        {
            "status": 0,
            "data": [
                {"sc": "SZ000001", "rk": 1},
                {"sc": "SH600000", "rk": 2},
            ],
        },
        name_by_code={"000001": "平安银行", "600000": "浦发银行"},
        expected_count=2,
    )

    assert [(stock.code, stock.name, stock.rank) for stock in stocks] == [
        ("000001", "平安银行", 1),
        ("600000", "浦发银行", 2),
    ]

    with pytest.raises(ValueError, match="不完整"):
        parse_eastmoney_hot_payload(
            {"status": 0, "data": [{"sc": "SZ000001", "rk": 1}]},
            name_by_code={"000001": "平安银行"},
            expected_count=2,
        )


def test_eastmoney_reply_payload_rejects_decoy_and_parses_public_replies() -> None:
    replies, total = parse_eastmoney_reply_payload(
        {
            "re": [
                {
                    "reply_id": "reply-1",
                    "reply_text": "盈利增长，明显利好",
                    "reply_publish_time": "2026-07-26 20:01:02",
                    "reply_like_count": 3,
                }
            ],
            "reply_total_count": 1,
        },
        code="000001",
        post_id="1749609162",
        collected_at=datetime(2026, 7, 26, 20, 5, tzinfo=BEIJING),
    )

    assert total == 1
    assert replies[0].content_id == "1749609162:reply-1"
    assert replies[0].content_kind == "reply"
    assert replies[0].likes == 3

    with pytest.raises(ValueError, match="非评论"):
        parse_eastmoney_reply_payload(
            {
                "re": True,
                "result": [{"security": "0$000001$123"}],
            },
            code="000001",
            post_id="1749609162",
            collected_at=datetime(2026, 7, 26, 20, 5, tzinfo=BEIJING),
        )


def test_sina_public_payloads_map_stock_topics_replies_likes_and_cursors() -> None:
    collected_at = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    config = parse_sina_bar_config(
        """
        <script>
        var PAGE_CONFIG = {
          "barName":"平安银行","bid":"477","isLogin":false,
          "stockMarket":"sz","stockCode":"000001"
        };
        </script>
        """,
        expected_code="000001",
    )
    topic_page = parse_sina_topic_payload(
        {
            "bid": "477",
            "data": {
                "topThreadList": [],
                "threads": [
                    {
                        "tid": "806853",
                        "title": "平安银行讨论",
                        "content": "盈利增长，明确利好",
                        "long_content": None,
                        "timestamp": 1785032551,
                        "reply": 2,
                        "like": 6,
                    }
                ],
            },
        },
        code="000001",
        bid="477",
        collected_at=collected_at,
    )
    replies = parse_sina_reply_payload(
        {
            "result": {
                "status": {"code": 0},
                "data": {
                    "data": [
                        {
                            "pid": "4",
                            "content": "亏损扩大，属于利空",
                            "ctimestamp": 1785036151,
                            "good": 3,
                        }
                    ]
                },
            }
        },
        code="000001",
        bid="477",
        tid="806853",
        collected_at=collected_at,
    )

    assert config.bid == "477"
    assert config.market == "sz"
    assert topic_page.references[0].reply_count == 2
    assert topic_page.contents[0].platform == "sina"
    assert topic_page.contents[0].likes == 6
    assert topic_page.contents[0].text == "平安银行讨论 盈利增长，明确利好"
    assert topic_page.next_relate_value == 1785032551
    assert topic_page.next_relate_tid == "806853"
    assert replies[0].platform == "sina"
    assert replies[0].content_id == "806853:4"
    assert replies[0].likes == 3
