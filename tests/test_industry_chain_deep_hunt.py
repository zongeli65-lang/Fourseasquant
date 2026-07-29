from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.control import HuntingRequestRecord
from fourseasquant.industry_chain.deep_hunt import (
    _has_positive_realization_evidence,
    _normalize_deep_hunt_payload,
    analyze_source_event,
)
from fourseasquant.industry_chain.deep_hunt import StaleDiscoveryError
import pytest
from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
)
from fourseasquant.industry_chain.ollama_runtime import OllamaStructuredRuntime


BEIJING = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 7, 25, 12, 0, tzinfo=BEIJING)


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "deep-hunt.db"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (
                'akshare_sina_daily', '2026-07-24', '000012', '玻璃公司',
                9.8, 10.2, 9.7, 10.0, 9.9, 1.0, 1000, 10000, 100
            )
            """
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES (
                '2026-07-24', 'akshare_sina_daily_qfq:2026-07-24',
                '2026-07-24T22:00:00+08:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO personal_fundamental_monthly_snapshots (
                code, as_of_date, rules_version, changed_fields_json,
                source_urls_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "000012",
                "2026-07-24",
                "personal-fundamental-v1",
                json.dumps(
                    {
                        "main_business_name": "玻璃产品",
                        "main_business_profit_share": 0.88,
                    }
                ),
                '["https://webapi.cninfo.com.cn/api/stock/p_stock2215"]',
                "2026-07-25T10:50:00+08:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO personal_fundamental_monthly_batches (
                as_of_date, rules_version, expected_codes_json,
                completed_codes_json, status, created_at, published_at
            ) VALUES (?, ?, ?, ?, 'published', ?, ?)
            """,
            (
                "2026-07-24",
                "personal-fundamental-v1",
                '["000012"]',
                '["000012"]',
                "2026-07-25T11:00:00+08:00",
                "2026-07-25T11:00:00+08:00",
            ),
        )
    return database


def _request(discovery_id: str) -> HuntingRequestRecord:
    return HuntingRequestRecord(
        request_id=f"request-{discovery_id.replace(':', '-')}",
        trigger_method="new_evidence",
        trigger_type="source_event",
        trigger_content=json.dumps({"discovery_ids": [discovery_id]}),
        source_url=None,
        as_of_time=AS_OF,
        priority=1,
        status="running",
        requested_at=AS_OF,
        started_at=AS_OF,
        completed_at=None,
        error_summary=None,
    )


def _runtime(payload: dict[str, object]) -> OllamaStructuredRuntime:
    def transport(
        _: str,
        request: Mapping[str, object],
    ) -> dict[str, object]:
        del request
        return {
            "model": "qwen3:14b",
            "message": {"content": json.dumps(payload, ensure_ascii=False)},
        }

    return OllamaStructuredRuntime(transport=transport)


def test_deep_hunt_normalization_preserves_all_valid_model_candidates() -> None:
    candidate = {
        "code": "000012",
        "profit_transmission_path": ["需求增加", "公司供货", "利润兑现"],
    }
    normalized = _normalize_deep_hunt_payload(
        {
            "candidates": [dict(candidate) for _ in range(4)],
            "unresolved_items": [f"待核查{i}" for i in range(13)],
        }
    )

    assert len(normalized["candidates"]) == 4  # type: ignore[arg-type]
    assert len(normalized["unresolved_items"]) == 13  # type: ignore[arg-type]


def test_deep_hunt_normalization_repairs_explicit_price_trigger() -> None:
    normalized = _normalize_deep_hunt_payload(
        {
            "event_valid": True,
            "policy_only": False,
            "title": "茅台自营门店明确提价",
            "summary": "飞天茅台自营售价上调",
            "affected_product_or_service": "飞天茅台",
            "gap_node": "终端销售",
            "trigger_types": [],
            "candidates": [],
            "unresolved_items": [],
        }
    )

    assert normalized["event_valid"] is True
    assert normalized["trigger_types"] == ["price_change"]


def test_deep_hunt_normalization_deduplicates_trigger_types() -> None:
    normalized = _normalize_deep_hunt_payload(
        {
            "event_valid": True,
            "policy_only": False,
            "title": "批量订单落地",
            "event_time": "2026-07-27T10:00:00+08:00",
            "trigger_types": [
                "company_order",
                "company_order",
                "order_delivery",
            ],
            "affected_product_or_service": "清洁能源船舶",
            "gap_node": "订单",
            "candidates": [],
            "summary": "公司新签批量订单。",
            "unresolved_items": [],
        }
    )

    assert normalized["trigger_types"] == [
        "company_order",
        "order_delivery",
    ]


def test_newly_signed_order_is_positive_realization_evidence() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=BEIJING)
    item = DiscoveryItem(
        discovery_id="cninfo:order",
        source_id="cninfo",
        external_id="order",
        security_code="000001",
        security_name="示例公司",
        headline="示例公司新签批量订单合计33亿元",
        published_at=now,
        collected_at=now,
        source_url="https://static.cninfo.com.cn/order.pdf",
        attachment_url=None,
        payload={
            "content": (
                "公司新签批量清洁能源订单合计约33亿元，"
                "订单排产至2029年。"
            ),
            "publication_time_known": True,
        },
    )

    assert _has_positive_realization_evidence(item) is True


def test_named_company_news_links_legacy_uncoded_discovery(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cls-news:legacy-uncoded",
        source_id="cls-news",
        external_id="legacy-uncoded",
        security_code=None,
        security_name=None,
        headline="玻璃公司：光伏玻璃已批量供货头部客户",
        published_at=AS_OF,
        collected_at=AS_OF,
        source_url="https://www.cls.cn/detail/legacy-uncoded",
        attachment_url=None,
        payload={
            "content": "玻璃公司表示光伏玻璃已批量供货头部客户，订单已开始交付。",
            "published_at_known": True,
            "publication_time_known": True,
        },
    )
    append_discovery_items(database, (item,))
    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(
            {
                "event_valid": True,
                "policy_only": False,
                "title": "光伏玻璃供货事件",
                "event_time": AS_OF.isoformat(),
                "trigger_types": ["order_delivery"],
                "affected_product_or_service": "光伏玻璃",
                "gap_node": "光伏玻璃生产",
                "candidates": [
                    {
                        "code": "000012",
                        "name": "玻璃公司",
                        "chain_node": "光伏玻璃生产",
                        "benefit_type": "direct",
                        "main_business_match": True,
                        "profit_transmission_path": [
                            "订单已开始交付",
                            "销售收入增加",
                            "主营利润受益",
                        ],
                        "realization_status": "current",
                        "expected_start": "2026-07-25",
                        "realization_basis": "公司已批量供货头部客户",
                        "uncertainties": [],
                    }
                ],
                "summary": "公司供货已兑现。",
                "unresolved_items": [],
            }
        ),
        now=AS_OF,
        eligible_company_codes=("000012",),
    )

    assert result.publication is not None
    assert result.rejected_candidates == ()


def test_targeted_reported_business_segments_close_missing_local_profile(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM personal_fundamental_monthly_snapshots")
        connection.execute("DELETE FROM personal_fundamental_monthly_batches")
    event = DiscoveryItem(
        discovery_id="cls-news:tianci-event",
        source_id="cls-news",
        external_id="tianci-event",
        security_code=None,
        security_name=None,
        headline="天赐材料：已供货钠电池电解液给头部企业",
        published_at=AS_OF,
        collected_at=AS_OF,
        source_url="https://www.cls.cn/detail/tianci-event",
        attachment_url=None,
        payload={
            "content": "天赐材料已供货钠电池电解液给头部企业。",
            "published_at_known": True,
            "publication_time_known": True,
        },
    )
    business = DiscoveryItem(
        discovery_id="eastmoney-discovery:tianci-business",
        source_id="eastmoney-discovery",
        external_id="tianci-business",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司主营构成：光伏玻璃",
        published_at=AS_OF.replace(year=2025),
        collected_at=AS_OF,
        source_url="https://emweb.securities.eastmoney.com/business",
        attachment_url=None,
        payload={
            "content": "玻璃公司主营构成：光伏玻璃",
            "main_business": "光伏玻璃",
            "revenue_share": 0.90,
            "gross_profit_share": 0.86,
            "evidence_kind": "reported_business_segments",
            "published_at_known": True,
            "publication_time_known": False,
            "publication_date_known": True,
        },
    )
    # 测试库的合格股票是 000012，因此用相同逻辑验证缺失本地快照的闭环。
    event = event.__class__(
        **{
            **event.__dict__,
            "headline": "玻璃公司：已供货光伏玻璃给头部企业",
            "payload": {
                **event.payload,
                "content": "玻璃公司已供货光伏玻璃给头部企业。",
            },
        }
    )
    append_discovery_items(database, (event, business))
    result = analyze_source_event(
        database,
        request=_request(event.discovery_id),
        runtime=_runtime(
            {
                "event_valid": True,
                "policy_only": False,
                "title": "光伏玻璃供货事件",
                "event_time": AS_OF.isoformat(),
                "trigger_types": ["order_delivery"],
                "affected_product_or_service": "光伏玻璃",
                "gap_node": "光伏玻璃生产",
                "candidates": [
                    {
                        "code": "000012",
                        "name": "玻璃公司",
                        "chain_node": "光伏玻璃生产",
                        "benefit_type": "direct",
                        "profit_transmission_path": [
                            "已供货头部客户",
                            "销售收入开始兑现",
                            "主营利润直接受益",
                        ],
                        "main_business_match": True,
                        "realization_status": "current",
                        "expected_start": "2026-07-25",
                        "realization_basis": "公司已供货头部企业",
                        "uncertainties": [],
                    }
                ],
                "summary": "供货已兑现，相关产品是主要业务。",
                "unresolved_items": [],
            }
        ),
        now=AS_OF,
        additional_discovery_ids=(business.discovery_id,),
        eligible_company_codes=("000012",),
    )
    with sqlite3.connect(database) as connection:
        snapshot_json = connection.execute(
            "SELECT snapshot_json FROM industry_chain_selections"
        ).fetchone()[0]

    assert result.publication is not None
    assert result.rejected_candidates == ()
    assert '"gross_profit_share":0.86' in str(snapshot_json)


def test_deep_hunt_publishes_candidate_only_with_direct_realization_and_main_business(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cninfo:glass-order",
        source_id="cninfo",
        external_id="glass-order",
        security_code="000012",
        security_name="玻璃公司",
        headline="关于签订重大玻璃供货合同的公告",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/glass-order",
        attachment_url=None,
        payload={
            "content": "公司签订重大玻璃供货合同，本季度开始交付。",
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃重大供货合同开始兑现",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["company_order"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃供应",
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "重大供货合同生效",
                    "玻璃产品本季度开始交付",
                    "主营业务收入和毛利润开始兑现",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "正式公告明确本季度开始交付",
                "uncertainties": ["合同实际执行金额仍以履约为准"],
            }
        ],
        "summary": "公司级重大订单已经形成需求兑现。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            """
            SELECT status, candidate_count, snapshot_json
            FROM industry_chain_selections
            """
        ).fetchone()

    assert result.publication is not None
    assert result.rejected_candidates == ()
    assert selection is not None
    assert selection[0:2] == ("selected", 1)
    assert '"gross_profit_share":0.88' in str(selection[2])


def test_empty_first_pass_gets_focused_company_evidence_review(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cninfo:focused-order",
        source_id="cninfo",
        external_id="focused-order",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司签订重大玻璃供货订单",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/focused-order",
        attachment_url=None,
        payload={
            "content": "公司签订重大玻璃供货订单，产品已经批量交付。",
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    first_pass = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃供货订单",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["company_order"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃供应",
        "candidates": [],
        "summary": "订单已经签订并开始交付。",
        "unresolved_items": ["需要进一步核对公司证据"],
    }
    focused_pass = {
        **first_pass,
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "重大玻璃供货订单形成新增需求",
                    "公司已经开始批量交付",
                    "主营玻璃产品收入和毛利润开始兑现",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "公司公告明确订单已经批量交付",
                "uncertainties": ["后续履约规模仍需跟踪"],
            }
        ],
        "unresolved_items": [],
    }
    responses = iter((first_pass, focused_pass))
    calls = 0

    def transport(
        _: str,
        request: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        del request
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(next(responses), ensure_ascii=False)
            },
        }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=OllamaStructuredRuntime(transport=transport),
        now=AS_OF,
    )

    assert calls == 2
    assert result.publication is not None
    assert result.publication.inserted is True
    assert len(result.decision.candidates) == 1


def test_capacity_expansion_without_demand_or_sales_evidence_is_not_a_candidate(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cninfo:capacity-only",
        source_id="cninfo",
        external_id="capacity-only",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司新建生产线全面投产",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/capacity-only",
        attachment_url=None,
        payload={
            "content": (
                "公司新建玻璃生产线全面投产，年产能提升30%。"
                "公告未披露新增订单、客户需求、产能利用率、销量、"
                "上网量、售价或利润影响。"
            ),
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃生产线投产",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["other_supply_demand"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "新增产能投产",
                    "假设销量随产能增长",
                    "主营收入和毛利润上升",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "生产线已经投产",
                "uncertainties": ["尚无新增需求或销售兑现证据"],
            }
        ],
        "summary": "只有供给能力提升，没有新增需求或销售兑现证据。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count FROM industry_chain_selections"
        ).fetchone()

    assert selection == ("evidence_insufficient", 0)
    assert any(
        "正式行业供需证据" in reason
        for reason in result.rejected_candidates
    )


def test_official_web_evidence_can_confirm_main_business_for_recalled_company(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (
                'akshare_sina_daily', '2026-07-24', '600001', '点名公司',
                9.8, 10.2, 9.7, 10.0, 9.9, 1.0, 1000, 10000, 100
            )
            """
        )
    item = DiscoveryItem(
        discovery_id="cninfo:web-business-order",
        source_id="cninfo",
        external_id="web-business-order",
        security_code="600001",
        security_name="点名公司",
        headline="点名公司玻璃产品主营及重大订单公告",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/web-business-order",
        attachment_url=None,
        payload={
            "content": (
                "点名公司主要从事玻璃产品生产和销售，玻璃产品属于"
                "公司主营业务。公司已签订重大玻璃供货合同，"
                "本季度开始批量交付。"
            ),
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃重大订单进入交付",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["company_order", "order_delivery"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "600001",
                "name": "点名公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "下游重大玻璃订单已经签订",
                    "公司主营玻璃产品本季度批量交付",
                    "玻璃主营收入和毛利润开始兑现",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "公司正式公告明确订单和批量交付",
                "uncertainties": ["实际金额以最终履约为准"],
            }
        ],
        "summary": "正式公告同时证明主营、订单和交付。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count, snapshot_json "
            "FROM industry_chain_selections"
        ).fetchone()

    assert result.rejected_candidates == ()
    assert selection is not None
    assert selection[0:2] == ("selected", 1)
    assert "公司正式披露" in str(selection[2])


def test_social_lead_can_publish_evidence_insufficient_but_not_candidate(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="xueqiu-social:lead",
        source_id="xueqiu-social",
        external_id="lead",
        security_code=None,
        security_name=None,
        headline="市场传闻某铜矿暂停生产",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://xueqiu.com/1/2",
        attachment_url=None,
        payload={"snippet": "尚无矿山官方说明。", "published_at_known": True},
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "铜矿停产传闻待核实",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["supply_contraction"],
        "affected_product_or_service": "铜精矿",
        "gap_node": "上游铜矿供应",
        "candidates": [],
        "summary": "仅有社交平台线索，不能证明供给收缩或公司受益。",
        "unresolved_items": ["缺少矿山或权威媒体正式证据"],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count FROM industry_chain_selections"
        ).fetchone()

    assert result.publication is not None
    assert selection == ("evidence_insufficient", 0)


def test_researched_company_evidence_can_bridge_uncoded_media_lead(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    lead = DiscoveryItem(
        discovery_id="cls-news:glass-demand",
        source_id="cls-news",
        external_id="glass-demand",
        security_code=None,
        security_name=None,
        headline="玻璃订单增长，部分规格交付趋紧",
        published_at=datetime(2026, 7, 25, 11, 20, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 21, tzinfo=BEIJING),
        source_url="https://www.cls.cn/detail/glass-demand",
        attachment_url=None,
        payload={
            "content": "行业订单增加，部分规格交付周期延长。",
            "published_at_known": True,
        },
    )
    company_evidence = DiscoveryItem(
        discovery_id="cninfo:glass-delivery",
        source_id="cninfo",
        external_id="glass-delivery",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司光伏玻璃批量供货公告",
        published_at=datetime(2026, 7, 25, 11, 40, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 41, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/glass-delivery",
        attachment_url=None,
        payload={
            "content": "公司光伏玻璃已经批量供货，本季度开始交付。",
            "published_at_known": True,
            "research_parent_request_id": "request-cls-news-glass-demand",
        },
    )
    append_discovery_items(database, (lead, company_evidence))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃订单增加并进入批量交付",
        "event_time": "2026-07-25T11:40:00+08:00",
        "trigger_types": ["demand_increase", "order_delivery"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "行业订单增加并出现交付趋紧",
                    "公司光伏玻璃本季度批量交付",
                    "主营玻璃产品收入和毛利润开始兑现",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "公司公告明确本季度批量交付",
                "uncertainties": ["实际交付金额以履约为准"],
            }
        ],
        "summary": "产业需求与公司批量供货形成两段证据链。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(lead.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
        additional_discovery_ids=(company_evidence.discovery_id,),
        eligible_company_codes=("000012",),
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count FROM industry_chain_selections"
        ).fetchone()

    assert result.rejected_candidates == ()
    assert selection == ("selected", 1)


def test_multiple_formal_sources_can_stitch_demand_and_company_realization(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    industry_demand = DiscoveryItem(
        discovery_id="cls-news:glass-shortage",
        source_id="cls-news",
        external_id="glass-shortage",
        security_code=None,
        security_name=None,
        headline="光伏玻璃需求增长，部分规格供不应求",
        published_at=datetime(2026, 7, 25, 10, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 10, 31, tzinfo=BEIJING),
        source_url="https://www.cls.cn/detail/glass-shortage",
        attachment_url=None,
        payload={
            "content": "下游光伏玻璃订单增加，部分规格供不应求。",
            "publication_time_known": True,
        },
    )
    company_operation = DiscoveryItem(
        discovery_id="cninfo:glass-commercial-production",
        source_id="cninfo",
        external_id="glass-commercial-production",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司光伏玻璃经营情况公告",
        published_at=datetime(2026, 7, 25, 11, 0, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 1, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/glass-commercial-production",
        attachment_url=None,
        payload={
            "content": "公司光伏玻璃已经规模化量产并保持稳定供应。",
            "publication_time_known": True,
        },
    )
    append_discovery_items(database, (industry_demand, company_operation))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "光伏玻璃需求增长并出现供应紧张",
        "event_time": "2026-07-25T10:30:00+08:00",
        "trigger_types": ["demand_increase"],
        "affected_product_or_service": "光伏玻璃",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "光伏玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "下游光伏玻璃订单增加并出现供不应求",
                    "公司主营光伏玻璃已规模化量产并稳定供应",
                    "当前需求由主营产品承接并形成收入和毛利润",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "行业需求与公司商业化经营由两份正式材料共同证明",
                "uncertainties": ["具体增量金额以公司后续披露为准"],
            }
        ],
        "summary": "行业需求和公司商业化供应来自不同正式来源。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(industry_demand.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
        additional_discovery_ids=(company_operation.discovery_id,),
        eligible_company_codes=("000012",),
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count, snapshot_json "
            "FROM industry_chain_selections"
        ).fetchone()

    assert result.rejected_candidates == ()
    assert selection is not None
    assert selection[0:2] == ("selected", 1)
    assert "多正式来源拼接" in str(selection[2])


def test_cross_source_stitching_rejects_company_product_mismatch(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    industry_demand = DiscoveryItem(
        discovery_id="cls-news:glass-demand-product-check",
        source_id="cls-news",
        external_id="glass-demand-product-check",
        security_code=None,
        security_name=None,
        headline="光伏玻璃需求增长",
        published_at=AS_OF,
        collected_at=AS_OF,
        source_url="https://www.cls.cn/detail/glass-demand-product-check",
        attachment_url=None,
        payload={
            "content": "光伏玻璃订单增加，需求持续增长。",
            "publication_time_known": True,
        },
    )
    wrong_product = DiscoveryItem(
        discovery_id="cninfo:seat-production",
        source_id="cninfo",
        external_id="seat-production",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司汽车座椅业务公告",
        published_at=AS_OF,
        collected_at=AS_OF,
        source_url="https://www.cninfo.com.cn/seat-production",
        attachment_url=None,
        payload={
            "content": "公司汽车座椅已经规模化量产并保持稳定供应。",
            "publication_time_known": True,
        },
    )
    append_discovery_items(database, (industry_demand, wrong_product))
    decision = _stitched_glass_decision()

    result = analyze_source_event(
        database,
        request=_request(industry_demand.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
        additional_discovery_ids=(wrong_product.discovery_id,),
        eligible_company_codes=("000012",),
    )

    assert any(
        "主体、产品和时间一致" in reason
        for reason in result.rejected_candidates
    )


def test_cross_source_stitching_rejects_stale_company_realization(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    industry_demand = DiscoveryItem(
        discovery_id="cls-news:glass-demand-time-check",
        source_id="cls-news",
        external_id="glass-demand-time-check",
        security_code=None,
        security_name=None,
        headline="光伏玻璃需求增长",
        published_at=AS_OF,
        collected_at=AS_OF,
        source_url="https://www.cls.cn/detail/glass-demand-time-check",
        attachment_url=None,
        payload={
            "content": "光伏玻璃订单增加，需求持续增长。",
            "publication_time_known": True,
        },
    )
    stale_company_operation = DiscoveryItem(
        discovery_id="cninfo:old-glass-production",
        source_id="cninfo",
        external_id="old-glass-production",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司光伏玻璃经营情况公告",
        published_at=AS_OF - timedelta(days=100),
        collected_at=AS_OF,
        source_url="https://www.cninfo.com.cn/old-glass-production",
        attachment_url=None,
        payload={
            "content": "公司光伏玻璃已经规模化量产并保持稳定供应。",
            "publication_time_known": True,
        },
    )
    append_discovery_items(
        database,
        (industry_demand, stale_company_operation),
    )
    decision = _stitched_glass_decision()

    result = analyze_source_event(
        database,
        request=_request(industry_demand.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
        additional_discovery_ids=(stale_company_operation.discovery_id,),
        eligible_company_codes=("000012",),
    )

    assert any(
        "主体、产品和时间一致" in reason
        for reason in result.rejected_candidates
    )


def test_cross_source_stitching_rejects_newer_formal_counter_evidence(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    industry_demand = DiscoveryItem(
        discovery_id="cls-news:glass-demand-counter-check",
        source_id="cls-news",
        external_id="glass-demand-counter-check",
        security_code=None,
        security_name=None,
        headline="光伏玻璃需求增长",
        published_at=datetime(2026, 7, 25, 10, 0, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 10, 1, tzinfo=BEIJING),
        source_url="https://www.cls.cn/detail/glass-demand-counter-check",
        attachment_url=None,
        payload={
            "content": "光伏玻璃订单增加，需求持续增长。",
            "publication_time_known": True,
        },
    )
    company_operation = DiscoveryItem(
        discovery_id="cninfo:glass-production-before-counter",
        source_id="cninfo",
        external_id="glass-production-before-counter",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司光伏玻璃经营情况公告",
        published_at=datetime(2026, 7, 25, 10, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 10, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/glass-production-before-counter",
        attachment_url=None,
        payload={
            "content": "公司光伏玻璃已经规模化量产并保持稳定供应。",
            "publication_time_known": True,
        },
    )
    counter_evidence = DiscoveryItem(
        discovery_id="sse:glass-counter-evidence",
        source_id="sse",
        external_id="glass-counter-evidence",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司光伏玻璃业务澄清公告",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.sse.com.cn/glass-counter-evidence",
        attachment_url=None,
        payload={
            "content": "公司光伏玻璃尚未形成批量销售，仍处于客户验证阶段。",
            "publication_time_known": True,
        },
    )
    append_discovery_items(
        database,
        (industry_demand, company_operation, counter_evidence),
    )
    decision = _stitched_glass_decision()

    result = analyze_source_event(
        database,
        request=_request(industry_demand.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
        additional_discovery_ids=(
            company_operation.discovery_id,
            counter_evidence.discovery_id,
        ),
        eligible_company_codes=("000012",),
    )

    assert any(
        "正式反证" in reason
        for reason in result.rejected_candidates
    )


def _stitched_glass_decision() -> dict[str, object]:
    return {
        "event_valid": True,
        "policy_only": False,
        "title": "光伏玻璃需求增长",
        "event_time": "2026-07-25T10:00:00+08:00",
        "trigger_types": ["demand_increase"],
        "affected_product_or_service": "光伏玻璃",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "000012",
                "name": "玻璃公司",
                "chain_node": "光伏玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": [
                    "光伏玻璃订单增加",
                    "公司主营光伏玻璃已商业化供应",
                    "主营收入和毛利润由当前需求承接",
                ],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "行业需求与公司经营材料共同证明",
                "uncertainties": [],
            }
        ],
        "summary": "行业需求和公司经营由不同正式来源共同证明。",
        "unresolved_items": [],
    }


def test_malformed_model_candidate_is_safely_removed_instead_of_failing_run(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cninfo:malformed-candidate",
        source_id="cninfo",
        external_id="malformed-candidate",
        security_code="000012",
        security_name="玻璃公司",
        headline="玻璃公司批量供货公告",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cninfo.com.cn/malformed-candidate",
        attachment_url=None,
        payload={
            "content": "公司玻璃产品已经批量供货。",
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": True,
        "policy_only": False,
        "title": "玻璃产品批量供货",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": ["order_delivery"],
        "affected_product_or_service": "玻璃产品",
        "gap_node": "玻璃生产",
        "candidates": [
            {
                "code": "000012.SZ",
                "name": "玻璃公司",
                "chain_node": "玻璃生产",
                "benefit_type": "direct",
                "profit_transmission_path": ["玻璃产品批量供货"],
                "main_business_match": True,
                "realization_status": "current",
                "expected_start": "",
                "realization_basis": "公告披露已经批量供货",
                "uncertainties": [],
            }
        ],
        "summary": "玻璃产品进入批量供货。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )
    with sqlite3.connect(database) as connection:
        selection = connection.execute(
            "SELECT status, candidate_count FROM industry_chain_selections"
        ).fetchone()

    assert result.publication is not None
    assert selection == ("evidence_insufficient", 0)
    assert any(
        "缺少完整利润传导路径" in item
        for item in result.decision.unresolved_items
    )


def test_invalid_event_may_leave_supply_demand_fields_empty(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="cls-news:unrelated",
        source_id="cls-news",
        external_id="unrelated",
        security_code=None,
        security_name=None,
        headline="公司发布年度分红方案",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://www.cls.cn/detail/unrelated",
        attachment_url=None,
        payload={"published_at_known": True},
    )
    append_discovery_items(database, (item,))
    decision = {
        "event_valid": False,
        "policy_only": False,
        "title": "分红方案不是产业链供需事件",
        "event_time": "2026-07-25T11:30:00+08:00",
        "trigger_types": [],
        "affected_product_or_service": "",
        "gap_node": "",
        "candidates": [],
        "summary": "分红不改变产业链供需。",
        "unresolved_items": [],
    }

    result = analyze_source_event(
        database,
        request=_request(item.discovery_id),
        runtime=_runtime(decision),
        now=AS_OF,
    )

    assert result.decision.event_valid is False
    assert result.publication is None
    with sqlite3.connect(database) as connection:
        audit = connection.execute(
            """
            SELECT outcome, decision_json
            FROM industry_chain_deep_hunt_audits
            WHERE request_id = ?
            """,
            (_request(item.discovery_id).request_id,),
        ).fetchone()
    assert audit is not None
    assert audit[0] == "invalid_event"
    assert '"event_valid":false' in str(audit[1])


def test_deep_hunt_rejects_stale_date_embedded_in_listing_url(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    item = DiscoveryItem(
        discovery_id="ths-discovery:stale",
        source_id="ths-discovery",
        external_id="stale",
        security_code=None,
        security_name=None,
        headline="英伟达要求暂停生产H20芯片",
        published_at=datetime(2026, 7, 25, 11, 30, tzinfo=BEIJING),
        collected_at=datetime(2026, 7, 25, 11, 31, tzinfo=BEIJING),
        source_url="https://news.10jqka.com.cn/20250822/c670558023.shtml",
        attachment_url=None,
        payload={"publication_time_known": False},
    )
    append_discovery_items(database, (item,))

    with pytest.raises(StaleDiscoveryError, match="超出 72 小时"):
        analyze_source_event(
            database,
            request=_request(item.discovery_id),
            runtime=_runtime({}),
            now=AS_OF,
        )
