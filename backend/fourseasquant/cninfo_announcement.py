from __future__ import annotations

import io
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import cast
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from pypdf import PdfReader
import pandas as pd


CNINFO_DETAIL_ENDPOINT = (
    "http://www.cninfo.com.cn/new/announcement/bulletin_detail"
)
CNINFO_STATIC_ROOT = "http://static.cninfo.com.cn"
USER_AGENT = "Mozilla/5.0 Fourseasquant/0.1"
NumberPair = tuple[float | None, float | None]
ByteFetcher = Callable[[str, bytes | None], bytes]


@dataclass(frozen=True)
class OfficialBuybackDocument:
    pdf_url: str
    amount_cny: float | None
    shares: float | None
    content_sha256: str
    local_path: str | None
    parse_error: str | None = None


def read_official_buyback_document(
    detail_url: str,
    *,
    fetch_bytes: ByteFetcher | None = None,
    storage_directory: Path | None = None,
) -> OfficialBuybackDocument:
    fetcher = fetch_bytes or _fetch_bytes
    parsed = urlparse(detail_url)
    parameters = parse_qs(parsed.query)
    announcement_id = _single_parameter(parameters, "announcementId")
    announcement_time = _single_parameter(parameters, "announcementTime")
    detail_query = urlencode(
        {
            "announceId": announcement_id,
            "flag": "false",
            "announceTime": announcement_time,
        }
    )
    detail_payload = json.loads(
        fetcher(f"{CNINFO_DETAIL_ENDPOINT}?{detail_query}", b"")
    )
    announcement = cast(
        dict[str, object],
        cast(dict[str, object], detail_payload)["announcement"],
    )
    adjunct_url = str(announcement["adjunctUrl"]).lstrip("/")
    pdf_url = f"{CNINFO_STATIC_ROOT}/{adjunct_url}"
    pdf_payload = fetcher(pdf_url, None)
    content_sha256 = hashlib.sha256(pdf_payload).hexdigest()
    local_path = _save_content_addressed_pdf(
        pdf_payload,
        content_sha256=content_sha256,
        storage_directory=storage_directory,
    )
    parse_error: str | None = None
    try:
        text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(pdf_payload)).pages
        )
        amount_cny, shares = extract_buyback_amount_and_shares(text)
    except Exception as error:
        amount_cny = None
        shares = None
        parse_error = f"{type(error).__name__}: {error}"
    return OfficialBuybackDocument(
        pdf_url=pdf_url,
        amount_cny=amount_cny,
        shares=shares,
        content_sha256=content_sha256,
        local_path=str(local_path) if local_path is not None else None,
        parse_error=parse_error,
    )


def enrich_cancelled_buyback_announcements(
    frame: pd.DataFrame,
    *,
    storage_directory: Path | None = None,
    document_reader: Callable[[str], OfficialBuybackDocument] | None = None,
) -> pd.DataFrame:
    enriched = frame.copy()
    for column in (
        "注销金额",
        "注销股份数量",
        "官方文件链接",
        "官方文件哈希",
        "官方文件本机路径",
        "官方解析错误",
    ):
        if column not in enriched:
            enriched[column] = None
    for row_index, row in enriched.iterrows():
        title = (
            str(row.get("公告标题", ""))
            .replace("<em>", "")
            .replace("</em>", "")
            .replace(" ", "")
        )
        if not (
            "注销" in title
            and any(phrase in title for phrase in ("完成", "实施完毕", "已注销"))
        ):
            continue
        try:
            document = (
                document_reader(str(row["公告链接"]))
                if document_reader is not None
                else read_official_buyback_document(
                    str(row["公告链接"]),
                    storage_directory=storage_directory,
                )
            )
        except Exception as error:
            enriched.at[row_index, "官方解析错误"] = (
                f"{type(error).__name__}: {error}"
            )
            continue
        enriched.at[row_index, "注销金额"] = document.amount_cny
        enriched.at[row_index, "注销股份数量"] = document.shares
        enriched.at[row_index, "官方文件链接"] = document.pdf_url
        enriched.at[row_index, "官方文件哈希"] = document.content_sha256
        enriched.at[row_index, "官方文件本机路径"] = document.local_path
        enriched.at[row_index, "官方解析错误"] = document.parse_error
    return enriched


def extract_buyback_amount_and_shares(text: str) -> NumberPair:
    normalized = re.sub(r"\s+", "", text.replace(",", ""))
    amount = _last_scaled_number(
        normalized,
        (
            r"(?:累计)?(?:已)?回购(?:成交)?(?:的)?(?:总)?金额"
            r"(?:为|：|:)?(?:人民币)?([0-9.]+)(亿|万)?元",
            r"(?:实际|本次)回购金额"
            r"(?:为|：|:)?(?:人民币)?([0-9.]+)(亿|万)?元",
            r"(?:回购)?成交(?:的)?总金额"
            r"(?:为|：|:)?(?:人民币)?([0-9.]+)(亿|万)?元",
        ),
    )
    shares = _last_scaled_number(
        normalized,
        (
            r"(?:累计)?(?:已)?回购(?:股份)?(?:数量|股数)"
            r"(?:为|：|:)?([0-9.]+)(亿|万)?股",
            r"(?:本次)?注销(?:的)?(?:回购)?股份(?:数量)?"
            r"(?:为|：|:)?([0-9.]+)(亿|万)?股",
        ),
    )
    return amount, shares


def _last_scaled_number(
    text: str,
    patterns: tuple[str, ...],
) -> float | None:
    matches: list[tuple[int, str, str | None]] = []
    for pattern in patterns:
        matches.extend(
            (match.start(), match.group(1), match.group(2))
            for match in re.finditer(pattern, text)
        )
    if not matches:
        return None
    _, raw_value, unit = max(matches, key=lambda item: item[0])
    multiplier = (
        {"亿": 100_000_000.0, "万": 10_000.0}.get(unit, 1.0)
        if unit is not None
        else 1.0
    )
    return float(raw_value) * multiplier


def _single_parameter(
    parameters: dict[str, list[str]],
    key: str,
) -> str:
    values = parameters.get(key, [])
    if len(values) != 1 or not values[0]:
        raise ValueError(f"巨潮公告链接缺少唯一参数 {key}")
    return values[0]


def _fetch_bytes(url: str, body: bytes | None) -> bytes:
    request = Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "http://www.cninfo.com.cn/",
        },
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=20) as response:
        return cast(bytes, response.read())


def _save_content_addressed_pdf(
    payload: bytes,
    *,
    content_sha256: str,
    storage_directory: Path | None,
) -> Path | None:
    if storage_directory is None:
        return None
    storage_directory.mkdir(parents=True, exist_ok=True)
    destination = storage_directory / f"{content_sha256}.pdf"
    if destination.exists():
        return destination
    with tempfile.NamedTemporaryFile(
        dir=storage_directory,
        suffix=".pdf.tmp",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    temporary_path.replace(destination)
    return destination
