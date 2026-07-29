from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.announcement_triage import (
    TriageDecision,
    TriageEventType,
)
from fourseasquant.industry_chain.control import read_hunting_requests
from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
)
from fourseasquant.industry_chain.event_funnel import (
    cluster_and_queue_actionable_events,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def _item(
    discovery_id: str,
    *,
    headline: str,
    brief: str,
    now: datetime,
    code: str = "002709",
    name: str = "天赐材料",
) -> DiscoveryItem:
    return DiscoveryItem(
        discovery_id=discovery_id,
        source_id="cls-news",
        external_id=discovery_id,
        security_code=code,
        security_name=name,
        headline=headline,
        published_at=now,
        collected_at=now,
        source_url=f"https://example.test/{discovery_id}",
        attachment_url=None,
        payload={"brief": brief},
    )


def _decision(
    discovery_id: str,
    *,
    event_type: TriageEventType = "major_order",
) -> TriageDecision:
    return TriageDecision(
        discovery_id=discovery_id,
        action="investigate",
        suspected_event_type=event_type,
        priority=1,
        reason="需要核验需求和公司兑现",
    )


def test_duplicate_reports_share_one_event_request(tmp_path: Path) -> None:
    database = tmp_path / "event-cluster.db"
    initialize_database(database)
    now = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    append_discovery_items(
        database,
        (
            _item(
                "news-1",
                headline="天赐材料获电解液批量订单",
                brief=(
                    "公司获得客户电解液批量订单，近期开始交付。"
                    "公告同时详细介绍了多条产线、历史沿革、客户区域、"
                    "技术路线和长期产能规划。"
                ),
                now=now,
            ),
            _item(
                "news-2",
                headline="天赐材料电解液订单近期交付",
                brief="客户电解液批量订单近期开始交付",
                now=now + timedelta(minutes=2),
            ),
        ),
    )

    result = cluster_and_queue_actionable_events(
        database,
        decisions=(_decision("news-1"), _decision("news-2")),
        now=now + timedelta(minutes=3),
    )

    assert result.cluster_count == 1
    assert result.queued_count == 1
    request = read_hunting_requests(database)[0]
    payload = json.loads(request.trigger_content)
    assert payload["cluster_evidence_count"] == 2
    assert set(payload["discovery_ids"]) == {"news-1", "news-2"}


def test_distinct_events_remain_separate(tmp_path: Path) -> None:
    database = tmp_path / "distinct-events.db"
    initialize_database(database)
    now = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    append_discovery_items(
        database,
        (
            _item(
                "order",
                headline="天赐材料获电解液批量订单",
                brief="客户订单开始交付",
                now=now,
            ),
            _item(
                "shutdown",
                headline="铜矿事故导致精矿供应中断",
                brief="海外铜矿事故停产，复产时间未定",
                now=now,
                code="000630",
                name="铜陵有色",
            ),
        ),
    )

    result = cluster_and_queue_actionable_events(
        database,
        decisions=(
            _decision("order"),
            _decision("shutdown", event_type="supply_contraction"),
        ),
        now=now,
    )

    assert result.cluster_count == 2
    assert result.queued_count == 2


def test_same_evidence_is_not_queued_twice(tmp_path: Path) -> None:
    database = tmp_path / "deduplicated-request.db"
    initialize_database(database)
    now = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    append_discovery_items(
        database,
        (
            _item(
                "news-1",
                headline="天赐材料获电解液批量订单",
                brief="客户订单开始交付",
                now=now,
            ),
        ),
    )
    decision = _decision("news-1")

    first = cluster_and_queue_actionable_events(
        database,
        decisions=(decision,),
        now=now,
    )
    second = cluster_and_queue_actionable_events(
        database,
        decisions=(decision,),
        now=now + timedelta(minutes=1),
    )

    assert first.queued_count == 1
    assert second.queued_count == 0
    assert len(read_hunting_requests(database)) == 1


def test_new_evidence_queues_one_event_revision(tmp_path: Path) -> None:
    database = tmp_path / "event-revision.db"
    initialize_database(database)
    now = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    first_item = _item(
        "news-1",
        headline="天赐材料获电解液批量订单",
        brief="客户电解液订单开始交付",
        now=now,
    )
    append_discovery_items(database, (first_item,))
    first = cluster_and_queue_actionable_events(
        database,
        decisions=(_decision("news-1"),),
        now=now,
    )
    second_item = _item(
        "news-2",
        headline="天赐材料电解液订单近期交付",
        brief="客户电解液批量订单已经开始交付",
        now=now + timedelta(minutes=5),
    )
    append_discovery_items(database, (second_item,))

    second = cluster_and_queue_actionable_events(
        database,
        decisions=(_decision("news-2"),),
        now=now + timedelta(minutes=5),
    )

    assert first.queued_count == 1
    assert second.queued_count == 1
    counts = sorted(
        json.loads(request.trigger_content)["cluster_evidence_count"]
        for request in read_hunting_requests(database)
    )
    assert counts == [1, 2]
