from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.cninfo_source import poll_cninfo_announcements


BEIJING = ZoneInfo("Asia/Shanghai")


def _raw(
    external_id: str,
    code: str,
    *,
    timestamp: datetime,
) -> dict[str, object]:
    return {
        "announcementId": external_id,
        "secCode": code,
        "secName": f"公司{code}",
        "orgId": f"org-{code}",
        "announcementTitle": f"{code} 重大订单公告",
        "announcementTime": int(timestamp.timestamp() * 1000),
        "adjunctUrl": f"finalpage/{external_id}.PDF",
        "adjunctSize": 120,
        "adjunctType": "PDF",
        "announcementType": "日常经营",
        "pageColumn": "SHMB",
    }


def test_cninfo_poll_filters_frozen_scope_and_stops_at_checkpoint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cninfo.db"
    initialize_database(database)
    current = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    pages: dict[int, list[dict[str, object]]] = {
        1: [
            _raw("new-main", "600001", timestamp=current.replace(hour=11)),
            _raw("new-growth", "300001", timestamp=current.replace(hour=10)),
        ],
        2: [
            _raw("old-main", "000001", timestamp=current.replace(hour=9)),
        ],
    }

    def fetch(payload: Mapping[str, str]) -> Mapping[str, object]:
        page = int(payload["pageNum"])
        return {
            "announcements": pages.get(page, []),
            "hasMore": page < 2,
        }

    first = poll_cninfo_announcements(
        database,
        eligible_codes=frozenset({"600001", "000001"}),
        start_date=date(2026, 7, 26),
        end_date=date(2026, 7, 26),
        as_of_time=current,
        fetch_page=fetch,
    )
    pages[1].insert(
        0,
        _raw("newer-main", "600002", timestamp=current.replace(hour=11, minute=30)),
    )
    second = poll_cninfo_announcements(
        database,
        eligible_codes=frozenset({"600001", "600002", "000001"}),
        start_date=date(2026, 7, 26),
        end_date=date(2026, 7, 26),
        as_of_time=current,
        fetch_page=fetch,
    )

    assert first.pages_read == 2
    assert first.announcements_seen == 3
    assert first.eligible_seen == 2
    assert first.inserted_ids == ("cninfo:new-main", "cninfo:old-main")
    assert second.pages_read == 1
    assert second.checkpoint_reached is True
    assert second.inserted_ids == ("cninfo:newer-main",)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT external_id, security_code
            FROM industry_chain_discovery_items
            ORDER BY published_at DESC
            """
        ).fetchall()
    assert rows == [
        ("newer-main", "600002"),
        ("new-main", "600001"),
        ("old-main", "000001"),
    ]


def test_cninfo_poll_does_not_save_future_announcement(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    initialize_database(database)
    current = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)

    result = poll_cninfo_announcements(
        database,
        eligible_codes=frozenset({"600001"}),
        start_date=current.date(),
        end_date=current.date(),
        as_of_time=current,
        fetch_page=lambda _: {
            "announcements": [
                _raw("future", "600001", timestamp=current.replace(hour=13))
            ],
            "hasMore": False,
        },
    )

    assert result.inserted_ids == ()
