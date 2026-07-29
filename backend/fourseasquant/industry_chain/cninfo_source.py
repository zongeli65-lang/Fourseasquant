from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
    read_source_checkpoint,
    save_source_checkpoint,
)
from fourseasquant.industry_chain.fresh_queue import (
    MAX_COLLECTED_PER_SOURCE_POLL,
    select_news_for_collection,
)


BEIJING = ZoneInfo("Asia/Shanghai")
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_DETAIL_URL = "https://www.cninfo.com.cn/new/disclosure/detail"
CNINFO_STATIC_URL = "https://static.cninfo.com.cn"
USER_AGENT = "Mozilla/5.0 Fourseasquant/0.1 public-disclosure-monitor"
PageFetcher = Callable[[Mapping[str, str]], Mapping[str, object]]


@dataclass(frozen=True)
class CninfoPollResult:
    pages_read: int
    announcements_seen: int
    eligible_seen: int
    inserted_ids: tuple[str, ...]
    checkpoint_external_id: str | None
    checkpoint_reached: bool


def poll_cninfo_announcements(
    path: Path,
    *,
    eligible_codes: frozenset[str],
    start_date: date,
    end_date: date,
    as_of_time: datetime,
    fetch_page: PageFetcher | None = None,
    maximum_pages: int = 100,
    update_checkpoint: bool = True,
) -> CninfoPollResult:
    if end_date < start_date:
        raise ValueError("巨潮采集结束日期不能早于开始日期")
    current = as_of_time.astimezone(BEIJING)
    checkpoint = read_source_checkpoint(path, "cninfo") if update_checkpoint else None
    fetcher = fetch_page or _fetch_page
    items: list[DiscoveryItem] = []
    pages_read = 0
    announcements_seen = 0
    checkpoint_reached = False
    newest_external_id: str | None = None
    newest_published_at: datetime | None = None

    for page_number in range(1, maximum_pages + 1):
        payload = _query_payload(
            page_number=page_number,
            start_date=start_date,
            end_date=end_date,
        )
        response = fetcher(payload)
        pages_read += 1
        announcements = cast(
            list[Mapping[str, object]],
            response.get("announcements") or [],
        )
        if not announcements:
            break
        for raw in announcements:
            announcements_seen += 1
            external_id = str(raw.get("announcementId") or "").strip()
            if not external_id:
                continue
            if newest_external_id is None:
                newest_external_id = external_id
                newest_published_at = _published_at(raw)
            if checkpoint is not None and external_id == checkpoint:
                checkpoint_reached = True
                break
            item = _normalize_announcement(raw, collected_at=current)
            if (
                item.security_code in eligible_codes
                and item.published_at <= current
                and start_date <= item.published_at.date() <= end_date
            ):
                items.append(item)
        if checkpoint_reached or not bool(response.get("hasMore")):
            break
    else:
        raise RuntimeError("巨潮公告分页超过安全上限")

    selected = select_news_for_collection(
        tuple(items),
        as_of_time=current,
        limit=MAX_COLLECTED_PER_SOURCE_POLL,
    )
    appended = append_discovery_items(path, selected)
    if update_checkpoint:
        save_source_checkpoint(
            path,
            source_id="cninfo",
            external_id=newest_external_id or checkpoint,
            published_at=newest_published_at,
            checked_at=current,
            status="succeeded",
        )
    return CninfoPollResult(
        pages_read=pages_read,
        announcements_seen=announcements_seen,
        eligible_seen=len(selected),
        inserted_ids=appended.inserted_ids,
        checkpoint_external_id=newest_external_id or checkpoint,
        checkpoint_reached=checkpoint_reached,
    )


def _query_payload(
    *,
    page_number: int,
    start_date: date,
    end_date: date,
) -> dict[str, str]:
    return {
        "pageNum": str(page_number),
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date.isoformat()}~{end_date.isoformat()}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }


def _fetch_page(payload: Mapping[str, str]) -> Mapping[str, object]:
    request = Request(
        CNINFO_QUERY_URL,
        data=urlencode(payload).encode(),
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.cninfo.com.cn/",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = response.read(10_000_000)
    return cast(Mapping[str, object], json.loads(body))


def _normalize_announcement(
    raw: Mapping[str, object],
    *,
    collected_at: datetime,
) -> DiscoveryItem:
    external_id = str(raw["announcementId"])
    security_code = str(raw.get("secCode") or "").zfill(6)
    security_name = str(raw.get("secName") or "").strip()
    headline = str(raw.get("announcementTitle") or "").strip()
    published_at = _published_at(raw)
    detail_query = urlencode(
        {
            "stockCode": security_code,
            "announcementId": external_id,
            "orgId": str(raw.get("orgId") or ""),
            "announcementTime": published_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    adjunct = str(raw.get("adjunctUrl") or "").lstrip("/")
    normalized_payload: dict[str, object] = {
        "external_id": external_id,
        "security_code": security_code,
        "security_name": security_name,
        "headline": headline,
        "published_at": published_at.isoformat(),
        "announcement_type": str(raw.get("announcementType") or ""),
        "page_column": str(raw.get("pageColumn") or ""),
        "attachment_size_kb": raw.get("adjunctSize"),
        "attachment_type": str(raw.get("adjunctType") or ""),
    }
    return DiscoveryItem(
        discovery_id=f"cninfo:{external_id}",
        source_id="cninfo",
        external_id=external_id,
        security_code=security_code,
        security_name=security_name or None,
        headline=headline,
        published_at=published_at,
        collected_at=collected_at,
        source_url=f"{CNINFO_DETAIL_URL}?{detail_query}",
        attachment_url=f"{CNINFO_STATIC_URL}/{adjunct}" if adjunct else None,
        payload=normalized_payload,
    )


def _published_at(raw: Mapping[str, object]) -> datetime:
    milliseconds = raw.get("announcementTime")
    if not isinstance(milliseconds, (int, float)):
        raise ValueError("巨潮公告缺少有效发布时间")
    return datetime.fromtimestamp(milliseconds / 1000, tz=BEIJING)
