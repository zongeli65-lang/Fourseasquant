from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.public_web import (
    ControlledPublicReader,
    PublicWebAccessError,
    PublicWebPolicy,
)


def _public_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    del host
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _private_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    del host
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


def _proxy_fake_ip_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    del host
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.23", port))]


def test_policy_blocks_credentials_private_network_and_unknown_sources(
    tmp_path: Path,
) -> None:
    database = tmp_path / "public-web.db"
    initialize_database(database)
    private_policy = PublicWebPolicy(database, resolver=_private_resolver)
    public_policy = PublicWebPolicy(database, resolver=_public_resolver)

    with pytest.raises(PublicWebAccessError, match="账号或密码"):
        public_policy.validate("https://user:secret@finance.eastmoney.com/a")
    with pytest.raises(PublicWebAccessError, match="私网"):
        private_policy.validate("https://finance.eastmoney.com/a")
    with pytest.raises(PublicWebAccessError, match="不在已登记"):
        public_policy.validate("https://example.com/a")

    assert (
        public_policy.validate(
            "https://example.com/a",
            allow_unregistered_public=True,
        )
        == "https://example.com/a"
    )
    assert (
        PublicWebPolicy(database, resolver=_proxy_fake_ip_resolver).validate(
            "https://finance.eastmoney.com/a"
        )
        == "https://finance.eastmoney.com/a"
    )
    with pytest.raises(PublicWebAccessError, match="内部网络主机名"):
        public_policy.validate(
            "https://service.internal/a",
            allow_unregistered_public=True,
        )


def test_reader_extracts_visible_html_and_revalidates_redirects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader.db"
    initialize_database(database)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                b"<html><style>hidden</style><body><h1>Supply news</h1>"
                b"<script>bad()</script><p>Main text</p></body></html>"
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reader = ControlledPublicReader(
        database,
        client=client,
        resolver=_public_resolver,
    )
    document = reader.read("https://finance.eastmoney.com/start")

    assert document.final_url == "https://finance.eastmoney.com/article"
    assert document.text == "Supply news\nMain text"
    assert len(document.sha256) == 64


def test_reader_safely_recognizes_html_when_response_omits_content_type(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-missing-content-type.db"
    initialize_database(database)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=(
                b"<!DOCTYPE html><html><head><title>Demand</title></head>"
                b"<body><p>Original article body</p></body></html>"
            ),
        )

    reader = ControlledPublicReader(
        database,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_public_resolver,
    )

    document = reader.read("https://www.cls.cn/detail/2437777")

    assert document.content_type == "text/html"
    assert document.text == "Demand\nOriginal article body"


def test_reader_rejects_unknown_binary_when_response_omits_content_type(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-unknown-binary.db"
    initialize_database(database)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"\x00\x01\x89PNG\r\n\x1a\n")

    reader = ControlledPublicReader(
        database,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_public_resolver,
    )

    with pytest.raises(PublicWebAccessError, match="未知"):
        reader.read("https://www.cls.cn/detail/2437777")


def test_reader_stops_oversized_stream_without_retaining_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reader-limit.db"
    initialize_database(database)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"123456",
        )

    reader = ControlledPublicReader(
        database,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_public_resolver,
        maximum_bytes=5,
    )

    with pytest.raises(PublicWebAccessError, match="大小上限"):
        reader.read("https://xueqiu.com/article")
