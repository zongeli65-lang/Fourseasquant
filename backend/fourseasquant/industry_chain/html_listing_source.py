from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from .discovery import DiscoveryItem, append_discovery_items
from .fresh_queue import (
    MAX_COLLECTED_PER_SOURCE_POLL,
    select_news_for_collection,
)
from .public_web import ControlledPublicReader
from .source_registry import read_latest_sources


ARTICLE_PATH_PATTERN = re.compile(r"(?:20\d{6}|\d{8,})")
ARTICLE_DATE_PATTERN = re.compile(r"(20\d{6})")
MAX_LISTING_AGE = timedelta(hours=72)


@dataclass(frozen=True)
class HtmlListingPollResult:
    links_seen: int
    eligible_links: int
    inserted_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Anchor:
    href: str
    title: str


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._parts: list[str] = []
        self.anchors: list[_Anchor] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a" or self._href is not None:
            return
        attributes = dict(attrs)
        self._href = attributes.get("href")
        self._parts = [attributes.get("title") or ""]

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        title = " ".join(" ".join(self._parts).split())
        self.anchors.append(_Anchor(self._href, title))
        self._href = None
        self._parts = []


def poll_html_listing(
    path: Path,
    *,
    source_id: str,
    as_of_time: datetime,
    reader: ControlledPublicReader | None = None,
    maximum_links: int = MAX_COLLECTED_PER_SOURCE_POLL,
) -> HtmlListingPollResult:
    if maximum_links < 1:
        raise ValueError("列表页最大链接数必须大于零")
    source = next(
        (
            item
            for item in read_latest_sources(path)
            if item.source_id == source_id
        ),
        None,
    )
    if source is None or source.lifecycle_state != "active":
        raise ValueError(f"来源未登记或未启用：{source_id}")
    owns_reader = reader is None
    selected_reader = reader or ControlledPublicReader(path)
    try:
        document = selected_reader.read(source.base_url)
    finally:
        if owns_reader:
            selected_reader.close()
    parser = _AnchorParser()
    parser.feed(_decode_html(document.content))
    accepted: list[DiscoveryItem] = []
    seen_urls: set[str] = set()
    for anchor in parser.anchors:
        normalized_url = _normalize_article_url(
            source.base_url,
            anchor.href,
            source.domain,
        )
        if normalized_url is None or normalized_url in seen_urls:
            continue
        title = anchor.title.strip()
        if not 8 <= len(title) <= 200:
            continue
        article_date = _article_date_from_url(normalized_url)
        if (
            article_date is not None
            and (
                article_date.date() > as_of_time.date()
                or datetime.combine(
                    article_date.date(),
                    time.max,
                    tzinfo=as_of_time.tzinfo,
                )
                < as_of_time - MAX_LISTING_AGE
            )
        ):
            continue
        seen_urls.add(normalized_url)
        external_id = hashlib.sha256(normalized_url.encode()).hexdigest()[:24]
        published_at = (
            article_date.replace(tzinfo=as_of_time.tzinfo)
            if article_date is not None
            else as_of_time
        )
        accepted.append(
            DiscoveryItem(
                discovery_id=f"{source_id}:{external_id}",
                source_id=source_id,
                external_id=external_id,
                security_code=None,
                security_name=None,
                headline=title,
                published_at=published_at,
                collected_at=as_of_time,
                source_url=normalized_url,
                attachment_url=None,
                payload={
                    "headline": title,
                    "listing_url": source.base_url,
                    "publication_time_known": False,
                    "publication_date_known": article_date is not None,
                    "first_seen_at": as_of_time.isoformat(),
                },
            )
        )
    selected = select_news_for_collection(
        tuple(accepted),
        as_of_time=as_of_time,
        limit=maximum_links,
    )
    appended = append_discovery_items(path, selected)
    return HtmlListingPollResult(
        links_seen=len(parser.anchors),
        eligible_links=len(selected),
        inserted_ids=appended.inserted_ids,
    )


def _normalize_article_url(
    base_url: str,
    href: str,
    registered_domain: str,
) -> str | None:
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return None
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    hostname = (parsed.hostname or "").lower()
    if not (
        hostname == registered_domain
        or hostname.endswith(f".{registered_domain}")
    ):
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not ARTICLE_PATH_PATTERN.search(parsed.path):
        return None
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            parsed.query,
            "",
        )
    )


def _decode_html(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _article_date_from_url(url: str) -> datetime | None:
    match = ARTICLE_DATE_PATTERN.search(urlparse(url).path)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None
