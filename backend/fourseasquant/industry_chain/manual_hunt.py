from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .control import HuntingRequestRecord
from .discovery import DiscoveryItem, append_discovery_items
from .public_web import ControlledPublicReader
from .source_registry import read_latest_sources


def ingest_manual_hunt(
    path: Path,
    *,
    request: HuntingRequestRecord,
    now: datetime,
    reader: ControlledPublicReader | None = None,
) -> tuple[str, ...]:
    if request.trigger_type == "message":
        content = request.trigger_content.strip()
        source_id = "user-message"
        source_url = f"local://industry-chain/{request.request_id}"
        headline = _headline(content)
        payload: dict[str, object] = {
            "content": content,
            "submitted_at": request.requested_at.isoformat(),
            "request_id": request.request_id,
        }
    elif request.trigger_type == "url" and request.source_url is not None:
        owns_reader = reader is None
        selected_reader = reader or ControlledPublicReader(path)
        try:
            document = selected_reader.read(
                request.source_url,
                allow_unregistered_public=True,
            )
        finally:
            if owns_reader:
                selected_reader.close()
        content = document.text.strip()
        if not content:
            raise ValueError("公开网页没有可分析的文字正文")
        source_id = _source_id_for_url(path, document.final_url)
        source_url = document.final_url
        headline = _headline(content)
        payload = {
            "content": content[:20_000],
            "content_truncated": len(content) > 20_000,
            "content_type": document.content_type,
            "document_sha256": document.sha256,
            "request_id": request.request_id,
        }
    else:
        raise ValueError("当前手动执行器只处理网址和消息正文")

    fingerprint = hashlib.sha256(
        f"{source_url}\n{content}".encode()
    ).hexdigest()
    item = DiscoveryItem(
        discovery_id=f"manual:{fingerprint[:32]}",
        source_id=source_id,
        external_id=fingerprint,
        security_code=None,
        security_name=None,
        headline=headline,
        published_at=now,
        collected_at=now,
        source_url=source_url,
        attachment_url=None,
        payload=payload,
    )
    return append_discovery_items(path, (item,)).inserted_ids


def _source_id_for_url(path: Path, url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    for source in read_latest_sources(path):
        if hostname == source.domain or hostname.endswith(f".{source.domain}"):
            return source.source_id
    return "unregistered-public"


def _headline(content: str) -> str:
    compact_lines = [" ".join(line.split()) for line in content.splitlines()]
    first = next((line for line in compact_lines if line), content.strip())
    return first[:300]
