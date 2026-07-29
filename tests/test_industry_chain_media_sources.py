from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.cls_source import poll_cls_telegraphs
from fourseasquant.industry_chain.html_listing_source import poll_html_listing
from fourseasquant.industry_chain.public_web import ControlledPublicReader


BEIJING = ZoneInfo("Asia/Shanghai")


def _public_resolver(host: str, port: int) -> list[tuple[object, ...]]:
    del host
    return [(2, 1, 6, "", ("93.184.216.34", port))]


def test_cls_adapter_is_incremental_and_keeps_full_public_content(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cls.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING)

    def fetch(_: str) -> dict[str, object]:
        return {
            "errno": 0,
            "data": {
                "roll_data": [
                    {
                        "id": 2437001,
                        "title": "铜矿发生供应中断",
                        "brief": "矿山暂停生产。",
                        "content": "矿山因事故暂停生产，复产时间未定。",
                        "ctime": int(now.timestamp()) - 60,
                        "author": "财联社",
                        "level": "A",
                        "subjects": [{"subject_name": "有色金属"}],
                        "stock_list": [],
                    }
                ]
            },
        }

    first = poll_cls_telegraphs(database, as_of_time=now, fetch_json=fetch)
    second = poll_cls_telegraphs(database, as_of_time=now, fetch_json=fetch)
    with sqlite3.connect(database) as connection:
        payload = connection.execute(
            """
            SELECT payload_json
            FROM industry_chain_discovery_items
            WHERE discovery_id = 'cls-news:2437001'
            """
        ).fetchone()

    assert first.inserted_ids == ("cls-news:2437001",)
    assert second.inserted_ids == ()
    assert second.checkpoint_reached is True
    assert payload is not None
    assert "复产时间未定" in str(payload[0])


def test_html_listing_adapter_extracts_only_article_links(
    tmp_path: Path,
) -> None:
    database = tmp_path / "listing.db"
    initialize_database(database)
    html = """
    <html><body>
      <a href="/a/202607263476543210.html">海外铜矿停产，供应预期收紧</a>
      <a href="/about">关于我们</a>
      <a href="https://example.com/20260726.html">站外文章不接收</a>
      <a href="/a/202607263476543210.html">重复链接</a>
    </body></html>
    """.encode()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=html,
            )
        )
    )
    reader = ControlledPublicReader(
        database,
        client=client,
        resolver=_public_resolver,
    )
    now = datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING)

    result = poll_html_listing(
        database,
        source_id="eastmoney-discovery",
        as_of_time=now,
        reader=reader,
    )

    assert result.links_seen == 4
    assert result.eligible_links == 1
    assert len(result.inserted_ids) == 1


def test_html_listing_adapter_rejects_url_date_older_than_catchup_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "listing-stale.db"
    initialize_database(database)
    html = """
    <html><body>
      <a href="/20250822/c670558023.shtml">英伟达要求暂停生产H20芯片</a>
    </body></html>
    """.encode()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=html,
            )
        )
    )
    reader = ControlledPublicReader(
        database,
        client=client,
        resolver=_public_resolver,
    )

    result = poll_html_listing(
        database,
        source_id="ths-discovery",
        as_of_time=datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING),
        reader=reader,
    )

    assert result.eligible_links == 0
    assert result.inserted_ids == ()
