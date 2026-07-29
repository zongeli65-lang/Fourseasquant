from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol, cast

from fourseasquant.automation import MacOSNotifier
from fourseasquant.sqlite_connection import open_database_connection


class SelectionNotifier(Protocol):
    def send(self, title: str, message: str) -> None: ...


def notify_selection(
    path: Path,
    *,
    snapshot: Mapping[str, object],
    now: datetime,
    notifier: SelectionNotifier | None = None,
) -> str:
    selection_id = cast(str, snapshot["selection_id"])
    selection_version = cast(int, snapshot["selection_version"])
    status = cast(str, snapshot["status"])
    event = cast(dict[str, object], snapshot["event"])
    candidates = cast(list[dict[str, object]], snapshot["candidates"])
    notification_id = (
        f"selection:{selection_id}:v{selection_version}:{status}"
    )
    with open_database_connection(path) as connection:
        existing = connection.execute(
            """
            SELECT delivery_status
            FROM industry_chain_notifications
            WHERE notification_id = ?
            """,
            (notification_id,),
        ).fetchone()
    if existing is not None:
        return cast(str, existing[0])
    if candidates:
        names = "、".join(cast(str, item["name"]) for item in candidates)
        title = f"产业链候选已发布 · {len(candidates)} 只"
        message = f"{event['title']}：{names}"
        notification_type = "selection_published"
    else:
        title = "产业链狩猎完成 · 暂无候选"
        message = f"{event['title']}：{snapshot['summary']}"
        notification_type = "selection_empty"
    delivery_status = "sent"
    try:
        (notifier or MacOSNotifier()).send(title, message)
    except Exception as error:
        delivery_status = f"failed:{type(error).__name__}"[:100]
    payload_json = json.dumps(
        {
            "selection_id": selection_id,
            "selection_version": selection_version,
            "status": status,
            "candidate_codes": [item["code"] for item in candidates],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with open_database_connection(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_notifications (
                notification_id, notification_type, event_id, selection_id,
                is_backfill, title, message, payload_json, delivery_status,
                created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                notification_type,
                cast(str, snapshot["event_id"]),
                selection_id,
                title,
                message,
                payload_json,
                delivery_status,
                now.isoformat(),
            ),
        )
    return delivery_status
