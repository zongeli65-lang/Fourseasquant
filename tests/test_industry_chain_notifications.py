from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.automation import RecordingNotifier
from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.notifications import notify_selection


BEIJING = ZoneInfo("Asia/Shanghai")


def test_selection_notification_is_sent_and_saved_once(tmp_path: Path) -> None:
    database = tmp_path / "notification.db"
    initialize_database(database)
    notifier = RecordingNotifier()
    snapshot = {
        "selection_id": "selection-1",
        "selection_version": 1,
        "event_id": "event-1",
        "status": "selected",
        "event": {"title": "玻璃订单开始交付"},
        "summary": "形成公司级需求兑现。",
        "candidates": [{"code": "000012", "name": "玻璃公司"}],
    }
    now = datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING)

    first = notify_selection(
        database,
        snapshot=snapshot,
        now=now,
        notifier=notifier,
    )
    notify_selection(
        database,
        snapshot=snapshot,
        now=now,
        notifier=notifier,
    )
    with sqlite3.connect(database) as connection:
        saved = connection.execute(
            """
            SELECT notification_type, delivery_status
            FROM industry_chain_notifications
            """
        ).fetchall()

    assert first == "sent"
    assert notifier.events[0].title == "产业链候选已发布 · 1 只"
    assert "玻璃公司" in notifier.events[0].message
    assert len(notifier.events) == 1
    assert saved == [("selection_published", "sent")]
