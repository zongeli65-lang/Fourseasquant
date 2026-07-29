from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
)
from fourseasquant.industry_chain.fresh_queue import (
    cancel_low_value_queued_event_hunts,
    prune_untriaged_news,
    read_fresh_news_funnel,
    select_news_for_collection,
)


BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 27, 15, 0, tzinfo=BEIJING)


def _item(
    discovery_id: str,
    headline: str,
    *,
    published_at: datetime = NOW,
    source_id: str = "cls-news",
    code: str | None = None,
    research_parent: str | None = None,
) -> DiscoveryItem:
    payload: dict[str, object] = {"brief": headline}
    if research_parent is not None:
        payload["research_parent_request_id"] = research_parent
    return DiscoveryItem(
        discovery_id=discovery_id,
        source_id=source_id,
        external_id=discovery_id,
        security_code=code,
        security_name="测试公司" if code else None,
        headline=headline,
        published_at=published_at,
        collected_at=NOW,
        source_url=f"https://example.test/{discovery_id}",
        attachment_url=None,
        payload=payload,
    )


def test_collection_keeps_small_high_value_set() -> None:
    items = (
        _item("generic", "今日投资舆情热点"),
        _item("policy", "商务部发布产业发展规划"),
        _item(
            "order",
            "测试公司签订重大订单并于本季度批量交付",
            source_id="cninfo",
            code="600001",
        ),
        _item("shortage", "关键材料供应中断并出现缺货涨价"),
        _item("capacity", "企业新建产线正式投产"),
    )

    selected = select_news_for_collection(
        items,
        as_of_time=NOW,
        limit=2,
    )

    assert tuple(item.discovery_id for item in selected) == (
        "order",
        "shortage",
    )


def test_market_and_abnormal_trading_descriptions_are_not_supply_events() -> None:
    items = (
        _item("sector", "电力板块震荡回升 立新能源8天7板"),
        _item("etf", "玩转ETF：机构称板块或迎修复机会"),
        _item(
            "abnormal",
            "股票交易异常波动公告",
            source_id="cninfo",
            code="600001",
        ),
    )

    selected = select_news_for_collection(
        items,
        as_of_time=NOW,
        limit=8,
    )

    assert selected == ()


def test_funnel_excludes_old_news_and_research_material(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fresh-funnel.db"
    initialize_database(database)
    fresh_triaged = _item("fresh-triaged", "公司获得重大订单")
    fresh_pending = _item("fresh-pending", "上游停产导致供应中断")
    old = _item(
        "old",
        "历史订单",
        published_at=NOW - timedelta(hours=73),
    )
    research = _item(
        "research",
        "公司补充研究材料",
        research_parent="request-1",
    )
    append_discovery_items(
        database,
        (fresh_triaged, fresh_pending, old, research),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO industry_chain_triage_runs (
                triage_run_id, source_id, discovery_ids_json,
                decisions_json, model, prompt_version,
                model_metrics_json, input_sha256, started_at,
                completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "triage-fresh",
                "cls-news",
                json.dumps([fresh_triaged.discovery_id]),
                "{}",
                "test-model",
                "test-prompt",
                "{}",
                "a" * 64,
                NOW.isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

    funnel = read_fresh_news_funnel(database, as_of_time=NOW)

    assert funnel.collected_count == 2
    assert funnel.triaged_count == 1
    assert funnel.pending_triage_count == 1
    assert funnel.research_material_count == 1


def test_backlog_pruning_keeps_only_highest_value_fresh_news(
    tmp_path: Path,
) -> None:
    database = tmp_path / "prune-funnel.db"
    initialize_database(database)
    items = (
        _item(
            "order",
            "测试公司签订重大订单并批量交付",
            source_id="cninfo",
            code="600001",
        ),
        _item("shortage", "关键材料停产导致供应中断"),
        _item("capacity", "普通项目新建产线"),
        _item("generic", "一般市场资讯"),
    )
    append_discovery_items(database, items)

    deleted = prune_untriaged_news(
        database,
        as_of_time=NOW,
        keep=2,
    )
    with sqlite3.connect(database) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT discovery_id FROM industry_chain_discovery_items"
            )
        }

    assert deleted == 2
    assert remaining == {"order", "shortage"}


def test_low_value_historical_event_queue_is_cancelled_with_audit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cancel-low-value.db"
    initialize_database(database)
    low = _item("market", "电力板块震荡回升 机构称估值修复")
    high = _item("order", "测试公司签订重大订单并批量交付")
    append_discovery_items(database, (low, high))
    with sqlite3.connect(database) as connection:
        for request_id, discovery_id in (
            ("hunt-low", low.discovery_id),
            ("hunt-high", high.discovery_id),
        ):
            connection.execute(
                """
                INSERT INTO industry_chain_hunting_requests (
                    request_id, trigger_method, trigger_type,
                    trigger_content, as_of_time, priority, status,
                    requested_at
                ) VALUES (?, 'new_evidence', 'source_event', ?, ?, 1,
                          'queued', ?)
                """,
                (
                    request_id,
                    json.dumps({"discovery_ids": [discovery_id]}),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )

    cancelled = cancel_low_value_queued_event_hunts(
        database,
        as_of_time=NOW,
    )
    with sqlite3.connect(database) as connection:
        rows = dict(
            connection.execute(
                """
                SELECT request_id, status
                FROM industry_chain_hunting_requests
                WHERE request_id IN ('hunt-low', 'hunt-high')
                """
            ).fetchall()
        )

    assert cancelled == 1
    assert rows == {"hunt-low": "cancelled", "hunt-high": "queued"}
