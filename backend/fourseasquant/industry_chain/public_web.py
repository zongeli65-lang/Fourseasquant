from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from pypdf import PdfReader

from .source_registry import registered_domains


MAX_PUBLIC_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_REDIRECTS = 5
ALLOWED_PORTS = {80, 443}
PROXY_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/pdf",
    "application/rss+xml",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/xml",
    "text/csv",
    "text/html",
    "text/plain",
    "text/xml",
}


class PublicWebAccessError(RuntimeError):
    """公开网页读取被安全策略拒绝或读取失败。"""


@dataclass(frozen=True)
class PublicWebDocument:
    requested_url: str
    final_url: str
    content_type: str
    content: bytes
    text: str
    sha256: str


Resolver = Callable[[str, int], list[tuple[object, ...]]]


def _default_resolver(hostname: str, port: int) -> list[tuple[object, ...]]:
    return list(socket.getaddrinfo(hostname, port))


class PublicWebPolicy:
    def __init__(
        self,
        database_path: Path,
        *,
        resolver: Resolver = _default_resolver,
    ) -> None:
        self._database_path = database_path
        self._resolver = resolver

    def validate(
        self,
        url: str,
        *,
        allow_unregistered_public: bool = False,
    ) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PublicWebAccessError("只允许有效的公开 HTTP 或 HTTPS 网址")
        if parsed.username or parsed.password:
            raise PublicWebAccessError("网址不能包含账号或密码")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise PublicWebAccessError("网址端口无效") from error
        if port not in ALLOWED_PORTS:
            raise PublicWebAccessError("只允许公开网页标准端口 80 或 443")

        hostname = parsed.hostname.rstrip(".").lower()
        if (
            hostname == "localhost"
            or hostname.endswith((".local", ".lan", ".internal"))
        ):
            raise PublicWebAccessError("禁止读取本机或内部网络主机名")
        is_registered = self._is_registered(hostname)
        if not allow_unregistered_public and not is_registered:
            raise PublicWebAccessError("该网址不在已登记的持续监控来源中")

        try:
            resolved = self._resolver(hostname, port)
        except OSError as error:
            raise PublicWebAccessError("网址域名解析失败") from error
        addresses = {
            str(item[4][0])
            for item in resolved
            if len(item) >= 5 and isinstance(item[4], tuple) and item[4]
        }
        if not addresses:
            raise PublicWebAccessError("网址未解析到可用地址")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as error:
                raise PublicWebAccessError("网址解析结果不是有效 IP 地址") from error
            if not ip.is_global and not _is_proxy_fake_ip(ip):
                raise PublicWebAccessError("禁止读取本机、私网或保留网络地址")
        return url

    def _is_registered(self, hostname: str) -> bool:
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in registered_domains(self._database_path)
        )


class ControlledPublicReader:
    def __init__(
        self,
        database_path: Path,
        *,
        client: httpx.Client | None = None,
        resolver: Resolver = _default_resolver,
        maximum_bytes: int = MAX_PUBLIC_DOWNLOAD_BYTES,
        request_timeout_seconds: float = 20.0,
    ) -> None:
        if maximum_bytes < 1:
            raise ValueError("maximum_bytes 必须大于零")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds 必须大于零")
        self._policy = PublicWebPolicy(database_path, resolver=resolver)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                request_timeout_seconds,
                connect=min(10.0, request_timeout_seconds),
            ),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None
        self._maximum_bytes = maximum_bytes

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ControlledPublicReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(
        self,
        url: str,
        *,
        allow_unregistered_public: bool = False,
    ) -> PublicWebDocument:
        requested_url = url
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            self._policy.validate(
                current_url,
                allow_unregistered_public=allow_unregistered_public,
            )
            with self._client.stream(
                "GET",
                current_url,
                headers={
                    "Accept": (
                        "text/html,application/json,application/pdf,text/plain,"
                        "text/csv,application/vnd.ms-excel,"
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet,application/zip"
                    ),
                    "User-Agent": "FourseasquantIndustryChain/0.1",
                },
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise PublicWebAccessError("网页重定向缺少目标地址")
                    if redirect_count >= MAX_REDIRECTS:
                        raise PublicWebAccessError("网页重定向次数过多")
                    current_url = urljoin(current_url, location)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    raise PublicWebAccessError(
                        f"公开网页返回 HTTP {response.status_code}"
                    ) from error

                content_type = _normalize_content_type(
                    response.headers.get("content-type")
                )
                if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                    raise PublicWebAccessError(
                        f"不允许读取的内容类型：{content_type}"
                    )
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        if int(declared_length) > self._maximum_bytes:
                            raise PublicWebAccessError("公开文件超过下载大小上限")
                    except ValueError as error:
                        raise PublicWebAccessError("网页响应大小标头无效") from error
                content = _read_bounded(response, self._maximum_bytes)
                if not content_type:
                    content_type = _infer_missing_content_type(content)
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise PublicWebAccessError("不允许读取的内容类型：未知")
                return PublicWebDocument(
                    requested_url=requested_url,
                    final_url=current_url,
                    content_type=content_type,
                    content=content,
                    text=_extract_text(content, content_type),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
        raise PublicWebAccessError("网页读取未完成")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            normalized = " ".join(data.split())
            if normalized:
                self.parts.append(normalized)


def _normalize_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _infer_missing_content_type(content: bytes) -> str:
    prefix = content[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    return ""


def _is_proxy_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in network for network in PROXY_FAKE_IP_NETWORKS)


def _read_bounded(response: httpx.Response, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > maximum_bytes:
            raise PublicWebAccessError("公开文件超过下载大小上限")
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_text(content: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        except Exception as error:
            raise PublicWebAccessError("PDF 正文解析失败") from error
    if content_type == "text/html":
        parser = _VisibleTextParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        return "\n".join(parser.parts)
    if content_type.startswith("text/") or content_type == "application/json":
        return content.decode("utf-8", errors="replace")
    return ""
