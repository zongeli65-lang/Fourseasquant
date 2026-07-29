from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.public_opinion import OpinionContent, OpinionSourceType
from fourseasquant.public_opinion_repository import HotDiscoveryStock


BEIJING = ZoneInfo("Asia/Shanghai")
EASTMONEY_BASE_URL = "https://guba.eastmoney.com/"


class SourcePausedError(RuntimeError):
    pass


@dataclass
class PublicSourceGuard:
    minimum_interval_seconds: float = 5.0
    last_request_at: datetime | None = None
    paused_until: datetime | None = None
    pause_reason: str | None = None
    consecutive_failures: int = 0

    def delay_before_request(self, now: datetime) -> float:
        if self.paused_until is not None and now < self.paused_until:
            raise SourcePausedError(self.pause_reason or "公开来源暂时停用")
        if self.paused_until is not None and now >= self.paused_until:
            self.paused_until = None
            self.pause_reason = None
            self.consecutive_failures = 0
        if self.last_request_at is None:
            return 0.0
        elapsed = (now - self.last_request_at).total_seconds()
        return max(0.0, self.minimum_interval_seconds - elapsed)

    def record_success(self, now: datetime) -> None:
        self.last_request_at = now
        self.consecutive_failures = 0

    def record_http_status(self, status_code: int, now: datetime) -> None:
        self.last_request_at = now
        if status_code == 429:
            self.paused_until = now + timedelta(minutes=30)
            self.pause_reason = "来源返回 429，暂停 30 分钟"
            return
        if status_code in {403, 401}:
            local_now = now.astimezone(BEIJING)
            next_day = local_now.date() + timedelta(days=1)
            self.paused_until = datetime.combine(
                next_day,
                time.min,
                tzinfo=BEIJING,
            )
            self.pause_reason = "来源拒绝公开访问，已停止当日采集"
            return
        if status_code >= 400:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                self.paused_until = now + timedelta(minutes=30)
                self.pause_reason = "来源连续失败 3 次，熔断 30 分钟"
            return
        self.consecutive_failures = 0


class EastmoneyListReference(BaseModel):
    content_id: str
    code: str
    url: str
    title: str
    post_type: int
    reply_count: int
    last_update_text: str


class SinaBarConfig(BaseModel):
    bid: str
    code: str
    name: str
    market: str


class SinaTopicReference(BaseModel):
    tid: str
    reply_count: int
    published_at: datetime


class SinaTopicPage(BaseModel):
    contents: list[OpinionContent]
    references: list[SinaTopicReference]
    next_relate_value: int | None
    next_relate_tid: str | None


class _EastmoneyListParser(HTMLParser):
    def __init__(self, code: str) -> None:
        super().__init__(convert_charrefs=True)
        self.code = code
        self.references: list[EastmoneyListReference] = []
        self._in_row = False
        self._field: str | None = None
        self._texts: dict[str, list[str]] = {}
        self._anchor: dict[str, str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "tr" and "listitem" in classes:
            self._in_row = True
            self._texts = {}
            self._anchor = None
        if not self._in_row:
            return
        for field in ("reply", "title", "update"):
            if field in classes:
                self._field = field
                self._texts.setdefault(field, [])
        if tag == "a" and self._field == "title":
            post_id = attributes.get("data-postid", "")
            href = attributes.get("href", "")
            if post_id and href:
                self._anchor = {
                    "content_id": post_id,
                    "href": href,
                    "post_type": attributes.get("data-posttype", "0"),
                }

    def handle_endtag(self, tag: str) -> None:
        if tag in {"div", "td"}:
            self._field = None
        if tag != "tr" or not self._in_row:
            return
        self._in_row = False
        if self._anchor is None:
            return
        title = " ".join(self._texts.get("title", [])).strip()
        reply_text = "".join(self._texts.get("reply", [])).strip()
        update_text = " ".join(self._texts.get("update", [])).strip()
        self.references.append(
            EastmoneyListReference(
                content_id=self._anchor["content_id"],
                code=self.code,
                url=urljoin(EASTMONEY_BASE_URL, self._anchor["href"]),
                title=title,
                post_type=int(self._anchor["post_type"] or 0),
                reply_count=int(reply_text or 0),
                last_update_text=update_text,
            )
        )

    def handle_data(self, data: str) -> None:
        if self._in_row and self._field is not None and data.strip():
            self._texts.setdefault(self._field, []).append(data.strip())


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def parse_eastmoney_list(
    page_html: str,
    *,
    code: str,
) -> list[EastmoneyListReference]:
    parser = _EastmoneyListParser(code)
    parser.feed(page_html)
    return parser.references


def parse_eastmoney_hot_payload(
    payload: dict[str, Any],
    *,
    name_by_code: dict[str, str],
    expected_count: int = 100,
) -> list[HotDiscoveryStock]:
    if payload.get("status") != 0 or not isinstance(payload.get("data"), list):
        raise ValueError("东方财富热榜响应状态异常")
    rows = cast(list[object], payload["data"])
    if len(rows) != expected_count:
        raise ValueError(
            f"东方财富热榜不完整：期望 {expected_count} 条，实际 {len(rows)} 条"
        )
    stocks: list[HotDiscoveryStock] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("东方财富热榜存在无效记录")
        row = cast(dict[str, object], raw)
        market_code = str(row.get("sc", ""))
        code = market_code[2:] if market_code[:2] in {"SH", "SZ", "BJ"} else ""
        if len(code) != 6 or not code.isdigit():
            raise ValueError("东方财富热榜股票代码无效")
        rank = int(str(row.get("rk", 0)))
        name = name_by_code.get(code)
        if name is None:
            raise ValueError(f"东方财富热榜股票 {code} 缺少本地证券名称")
        stocks.append(HotDiscoveryStock(code=code, name=name, rank=rank))
    if sorted(stock.rank for stock in stocks) != list(
        range(1, expected_count + 1)
    ):
        raise ValueError("东方财富热榜排名不连续")
    return sorted(stocks, key=lambda stock: stock.rank)


def parse_eastmoney_detail(
    page_html: str,
    *,
    code: str,
    collected_at: datetime,
) -> OpinionContent:
    payload = _find_post_payload(page_html)
    content_id = str(payload["post_id"])
    published_at = datetime.strptime(
        str(payload["post_publish_time"]),
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=BEIJING)
    post_type = int(payload.get("post_type", 0))
    source_type: OpinionSourceType = (
        "media" if post_type in {1, 20} else "user_original"
    )
    title = str(payload.get("post_title", "")).strip()
    body = _plain_text(str(payload.get("post_content", "")))
    return OpinionContent(
        platform="eastmoney",
        content_id=content_id,
        code=code,
        url=f"{EASTMONEY_BASE_URL}news,{code},{content_id}.html",
        published_at=published_at,
        collected_at=collected_at,
        likes=max(0, int(payload.get("post_like_count") or 0)),
        content_kind="topic",
        source_type=source_type,
        text=" ".join(part for part in (title, body) if part),
    )


def parse_eastmoney_reply_payload(
    payload: dict[str, Any],
    *,
    code: str,
    post_id: str,
    collected_at: datetime,
) -> tuple[list[OpinionContent], int]:
    rows = payload.get("re")
    total = payload.get("reply_total_count")
    if not isinstance(rows, list) or not isinstance(total, int):
        raise ValueError("东方财富回复接口返回非评论数据")
    replies: list[OpinionContent] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("东方财富回复列表存在无效记录")
        row = cast(dict[str, object], raw)
        reply_id = str(row.get("reply_id", ""))
        reply_text = _plain_text(str(row.get("reply_text", "")))
        publish_time = str(row.get("reply_publish_time", ""))
        if not reply_id or not publish_time:
            raise ValueError("东方财富回复缺少编号或发布时间")
        published_at = datetime.strptime(
            publish_time,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=BEIJING)
        likes_value = row.get("reply_like_count")
        likes = int(str(likes_value)) if likes_value is not None else None
        replies.append(
            OpinionContent(
                platform="eastmoney",
                content_id=f"{post_id}:{reply_id}",
                code=code,
                url=f"{EASTMONEY_BASE_URL}news,{code},{post_id}.html#{reply_id}",
                published_at=published_at,
                collected_at=collected_at,
                likes=likes,
                content_kind="reply",
                source_type="user_original",
                text=reply_text,
            )
        )
    return replies, total


def parse_sina_bar_config(
    page_html: str,
    *,
    expected_code: str,
) -> SinaBarConfig:
    match = re.search(
        r"var\s+PAGE_CONFIG\s*=\s*(\{.*?\})\s*;",
        page_html,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError("新浪股吧页面缺少股票配置")
    payload = cast(dict[str, object], json.loads(match.group(1)))
    code = str(payload.get("stockCode", ""))
    bid = str(payload.get("bid", ""))
    market = str(payload.get("stockMarket", "")).lower()
    name = str(payload.get("barName", "")).strip()
    if code != expected_code or not bid or market not in {"sh", "sz", "bj"}:
        raise ValueError("新浪股吧股票配置与请求代码不一致")
    if not name:
        raise ValueError("新浪股吧股票配置缺少名称")
    return SinaBarConfig(
        bid=bid,
        code=code,
        name=name,
        market=market,
    )


def parse_sina_topic_payload(
    payload: dict[str, Any],
    *,
    code: str,
    bid: str,
    collected_at: datetime,
) -> SinaTopicPage:
    if str(payload.get("bid", "")) != bid:
        raise ValueError("新浪股吧主题响应编号不一致")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("新浪股吧主题响应缺少数据")
    rows = data.get("threads")
    if not isinstance(rows, list):
        raise ValueError("新浪股吧主题列表格式无效")
    contents: list[OpinionContent] = []
    references: list[SinaTopicReference] = []
    next_value: int | None = None
    next_tid: str | None = None
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("新浪股吧主题列表存在无效记录")
        row = cast(dict[str, object], raw)
        tid = str(row.get("tid", ""))
        timestamp = _required_nonnegative_int(
            row.get("timestamp"),
            field="新浪股吧主题时间",
        )
        if not tid:
            raise ValueError("新浪股吧主题缺少编号")
        published_at = datetime.fromtimestamp(timestamp, tz=BEIJING)
        title = _plain_optional_text(row.get("title"))
        long_content = _plain_optional_text(row.get("long_content"))
        short_content = _plain_optional_text(row.get("content"))
        text = " ".join(
            part
            for part in (
                title,
                long_content or short_content,
            )
            if part
        )
        contents.append(
            OpinionContent(
                platform="sina",
                content_id=tid,
                code=code,
                url=f"https://guba.sina.cn/view_{bid}_{tid}.html",
                published_at=published_at,
                collected_at=collected_at,
                likes=_optional_sina_count(row.get("like")),
                content_kind="topic",
                source_type="user_original",
                text=text,
            )
        )
        references.append(
            SinaTopicReference(
                tid=tid,
                reply_count=_required_nonnegative_int(
                    row.get("reply", 0),
                    field="新浪股吧回复数量",
                ),
                published_at=published_at,
            )
        )
        next_value = timestamp
        next_tid = tid
    return SinaTopicPage(
        contents=contents,
        references=references,
        next_relate_value=next_value,
        next_relate_tid=next_tid,
    )


def parse_sina_reply_payload(
    payload: dict[str, Any],
    *,
    code: str,
    bid: str,
    tid: str,
    collected_at: datetime,
) -> list[OpinionContent]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("新浪股吧回复响应缺少结果")
    status = result.get("status")
    data = result.get("data")
    if (
        not isinstance(status, dict)
        or str(status.get("code")) != "0"
        or not isinstance(data, dict)
        or not isinstance(data.get("data"), list)
    ):
        raise ValueError("新浪股吧回复响应状态异常")
    replies: list[OpinionContent] = []
    for raw in cast(list[object], data["data"]):
        if not isinstance(raw, dict):
            raise ValueError("新浪股吧回复列表存在无效记录")
        row = cast(dict[str, object], raw)
        pid = str(row.get("pid", ""))
        timestamp = _required_nonnegative_int(
            row.get("ctimestamp"),
            field="新浪股吧回复时间",
        )
        if not pid:
            raise ValueError("新浪股吧回复缺少编号")
        replies.append(
            OpinionContent(
                platform="sina",
                content_id=f"{tid}:{pid}",
                code=code,
                url=f"https://guba.sina.cn/view_{bid}_{tid}.html#{pid}",
                published_at=datetime.fromtimestamp(timestamp, tz=BEIJING),
                collected_at=collected_at,
                likes=_optional_sina_count(row.get("good")),
                content_kind="reply",
                source_type="user_original",
                text=_plain_optional_text(row.get("content")),
            )
        )
    return replies


def _required_nonnegative_int(value: object, *, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}格式无效") from error
    if parsed < 0:
        raise ValueError(f"{field}不能为负数")
    return parsed


def _optional_sina_count(value: object) -> int:
    if value is None or value == "":
        return 0
    return _required_nonnegative_int(value, field="新浪股吧点赞数量")


def _find_post_payload(page_html: str) -> dict[str, Any]:
    marker = '"post_publish_time"'
    marker_at = page_html.find(marker)
    if marker_at < 0:
        raise ValueError("东方财富详情页缺少原始发布时间")
    decoder = json.JSONDecoder()
    starts = [
        match.start()
        for match in re.finditer(r"\{", page_html[:marker_at])
    ]
    for start in reversed(starts):
        try:
            payload, _ = decoder.raw_decode(page_html[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "post_id" in payload:
            return cast(dict[str, Any], payload)
    raise ValueError("东方财富详情页未找到公开帖子数据")


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(html.unescape(value))
    return " ".join(parser.parts)


def _plain_optional_text(value: object) -> str:
    return _plain_text(value) if isinstance(value, str) else ""
