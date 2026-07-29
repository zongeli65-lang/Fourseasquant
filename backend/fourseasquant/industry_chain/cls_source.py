from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .discovery import (
    DiscoveryItem,
    append_discovery_items,
    read_source_checkpoint,
    save_source_checkpoint,
)
from .fresh_queue import select_news_for_collection
from .public_web import ControlledPublicReader


BEIJING = ZoneInfo("Asia/Shanghai")
CLS_API_URL = "https://www.cls.cn/api/cache"
JsonFetcher = Callable[[str], Mapping[str, object]]


@dataclass(frozen=True)
class ClsPollResult:
    items_seen: int
    inserted_ids: tuple[str, ...]
    checkpoint_external_id: str | None
    checkpoint_reached: bool


def poll_cls_telegraphs(
    path: Path,
    *,
    as_of_time: datetime,
    fetch_json: JsonFetcher | None = None,
    update_checkpoint: bool = True,
) -> ClsPollResult:
    current = as_of_time.astimezone(BEIJING)
    checkpoint = read_source_checkpoint(path, "cls-news") if update_checkpoint else None
    query = urlencode(
        {
            "rn": "20",
            "lastTime": str(int(current.timestamp())),
            "name": "telegraph",
        }
    )
    fetcher = fetch_json or _reader_fetcher(path)
    response = fetcher(f"{CLS_API_URL}?{query}")
    data = cast(Mapping[str, object], response.get("data") or {})
    raw_items = cast(list[Mapping[str, object]], data.get("roll_data") or [])
    items: list[DiscoveryItem] = []
    newest_external_id: str | None = None
    newest_published_at: datetime | None = None
    checkpoint_reached = False
    for raw in raw_items:
        external_id = str(raw.get("id") or "").strip()
        if not external_id:
            continue
        if newest_external_id is None:
            newest_external_id = external_id
            newest_published_at = _published_at(raw)
        if checkpoint is not None and external_id == checkpoint:
            checkpoint_reached = True
            break
        item = _normalize_telegraph(raw, collected_at=current)
        if item.published_at <= current:
            items.append(item)
    selected = select_news_for_collection(
        tuple(items),
        as_of_time=current,
    )
    appended = append_discovery_items(path, selected)
    if update_checkpoint:
        save_source_checkpoint(
            path,
            source_id="cls-news",
            external_id=newest_external_id or checkpoint,
            published_at=newest_published_at,
            checked_at=current,
            status="succeeded",
        )
    return ClsPollResult(
        items_seen=len(raw_items),
        inserted_ids=appended.inserted_ids,
        checkpoint_external_id=newest_external_id or checkpoint,
        checkpoint_reached=checkpoint_reached,
    )


def _reader_fetcher(path: Path) -> JsonFetcher:
    def fetch(url: str) -> Mapping[str, object]:
        with ControlledPublicReader(path) as reader:
            document = reader.read(url)
        return cast(Mapping[str, object], json.loads(document.content))

    return fetch


def _normalize_telegraph(
    raw: Mapping[str, object],
    *,
    collected_at: datetime,
) -> DiscoveryItem:
    external_id = str(raw["id"])
    title = str(raw.get("title") or "").strip()
    brief = str(raw.get("brief") or "").strip()
    content = str(raw.get("content") or "").strip()
    headline = title or brief or content
    if len(headline) > 300:
        headline = f"{headline[:297]}..."
    stock_list = raw.get("stock_list")
    subjects = raw.get("subjects")
    payload: dict[str, object] = {
        "title": title,
        "brief": brief,
        "content": content,
        "author": str(raw.get("author") or ""),
        "level": str(raw.get("level") or ""),
        "subjects": subjects if isinstance(subjects, list) else [],
        "stock_list": stock_list if isinstance(stock_list, list) else [],
        "published_at": _published_at(raw).isoformat(),
    }
    security_code, security_name = _stock_identity(stock_list)
    return DiscoveryItem(
        discovery_id=f"cls-news:{external_id}",
        source_id="cls-news",
        external_id=external_id,
        security_code=security_code,
        security_name=security_name,
        headline=headline,
        published_at=_published_at(raw),
        collected_at=collected_at,
        source_url=f"https://www.cls.cn/detail/{external_id}",
        attachment_url=None,
        payload=payload,
    )


def _stock_identity(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, list) or len(value) != 1:
        return None, None
    raw = value[0]
    if not isinstance(raw, Mapping):
        return None, None
    stock_id = str(raw.get("StockID") or "").strip().lower()
    code = stock_id.removeprefix("sh").removeprefix("sz")
    if len(code) != 6 or not code.isdigit():
        return None, None
    name = str(raw.get("name") or "").strip()
    return code, name or None


def _published_at(raw: Mapping[str, object]) -> datetime:
    timestamp = raw.get("ctime")
    if not isinstance(timestamp, (int, float)):
        raise ValueError("财联社电报缺少有效发布时间")
    return datetime.fromtimestamp(timestamp, tz=BEIJING)
