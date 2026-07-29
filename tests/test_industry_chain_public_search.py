from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.company_evidence import LocalCompanyEvidence
from fourseasquant.industry_chain.control import (
    HuntingRequestRecord,
    claim_next_manual_hunt,
    claim_next_source_event_hunt,
    enqueue_source_event_hunt,
    set_hunting_enabled,
    submit_manual_hunt,
)
from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
)
from fourseasquant.industry_chain.ollama_runtime import OllamaStructuredRuntime
from fourseasquant.industry_chain.public_search import (
    ChainNode,
    EventResearchPlan,
    PublicSearchResult,
    SearchInstruction,
    _ResearchHit,
    _BingHtmlResultParser,
    _SoHtmlResultParser,
    _collect_research_hits,
    _company_search_instructions,
    _enrich_publication_time_from_html,
    _fallback_gap_search_plan,
    _relationship_discovery_instructions,
    _research_discovery_items,
    _result_matches_instruction,
    _url_date,
    _with_inferred_publication_time,
    execute_keyword_search,
    execute_source_event_research,
)
from fourseasquant.industry_chain.source_registry import RegisteredSource
from fourseasquant.industry_chain.universe import (
    EligibleSecurity,
    EligibleUniverseSnapshot,
)
from fourseasquant.industry_chain.triage_repository import (
    read_untriaged_discovery_ids,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_bing_html_parser_extracts_result_rows() -> None:
    parser = _BingHtmlResultParser()
    parser.feed(
        """
        <ol>
          <li class="b_algo">
            <h2><a href="https://finance.eastmoney.com/a/1">标题一</a></h2>
            <div class="b_caption"><p>摘要一</p></div>
          </li>
          <li class="b_algo">
            <h2><a href="https://www.cninfo.com.cn/a/2">标题二</a></h2>
            <p>摘要二</p>
          </li>
        </ol>
        """
    )
    parser.close()

    assert [(row.title, row.url, row.snippet) for row in parser.rows] == [
        ("标题一", "https://finance.eastmoney.com/a/1", "摘要一"),
        ("标题二", "https://www.cninfo.com.cn/a/2", "摘要二"),
    ]


def test_so_html_parser_prefers_original_result_url() -> None:
    parser = _SoHtmlResultParser()
    parser.feed(
        """
        <ul class="result">
          <li class="res-list">
            <h3 class="res-title">
              <a href="https://www.so.com/link?m=redirect"
                 data-mdurl="https://finance.eastmoney.com/a/1">
                标题一
              </a>
            </h3>
            <p class="res-desc">摘要一</p>
          </li>
          <li class="res-list">
            <h3><a data-mdurl="https://www.cninfo.com.cn/a/2">标题二</a></h3>
            <span class="res-list-summary">摘要二</span>
          </li>
        </ul>
        """
    )
    parser.close()

    assert [(row.title, row.url, row.snippet) for row in parser.rows] == [
        ("标题一", "https://finance.eastmoney.com/a/1", "摘要一"),
        ("标题二", "https://www.cninfo.com.cn/a/2", "摘要二"),
    ]


def test_page_visible_header_can_supply_exact_publication_time() -> None:
    reference = datetime(2026, 7, 26, 21, 45, tzinfo=BEIJING)
    result = _enrich_publication_time_from_html(
        PublicSearchResult(
            title="产业新闻",
            url="https://finance.eastmoney.com/a/202607260001.html",
            snippet="摘要",
            published_at=reference.replace(hour=0, minute=0),
            publication_time_known=False,
        ),
        content=(
            b"<html><script>var fake='2099-01-01 00:00';</script>"
            b"<body><h1>Industry news</h1>"
            b"<div class='time'>2026-07-26 21:37</div></body></html>"
        ),
        reference=reference,
    )

    assert result.publication_time_known is True
    assert result.published_at == datetime(
        2026,
        7,
        26,
        21,
        37,
        tzinfo=BEIJING,
    )


def test_cninfo_hyphenated_url_supplies_publication_date() -> None:
    reference = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)

    assert _url_date(
        "https://static.cninfo.com.cn/finalpage/2026-01-30/1224955451.PDF",
        reference=reference,
    ) == datetime(2026, 1, 30, tzinfo=BEIJING)


def test_event_date_in_search_snippet_is_not_treated_as_publication_time() -> None:
    reference = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)
    result = _with_inferred_publication_time(
        PublicSearchResult(
            title="公司签署重大合同",
            url="https://finance.eastmoney.com/a/unknown.html",
            snippet="合同于2026-07-27签署",
            published_at=datetime(2026, 7, 27, tzinfo=BEIJING),
            publication_time_known=False,
        ),
        reference=reference,
    )

    assert result.publication_time_known is False
    assert result.published_at == datetime(2026, 7, 27, tzinfo=BEIJING)


def test_non_listed_event_entity_generates_ownership_search() -> None:
    now = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)
    universe = EligibleUniverseSnapshot(
        universe_version="test",
        available_as_of_time=now,
        actual_data_date=now.date(),
        source="test",
        source_published_at=now,
        eligibility_rules_version="test",
        minimum_listing_trading_days=60,
        securities=(
            EligibleSecurity(
                code="000039",
                name="中集集团",
                listing_trading_days=300,
            ),
        ),
    )
    source = RegisteredSource(
        source_id="cninfo",
        source_version=1,
        source_name="巨潮资讯",
        base_url="https://www.cninfo.com.cn/",
        domain="cninfo.com.cn",
        source_tier=1,
        source_type="exchange_disclosure",
        categories=("announcement",),
        access_class="public_no_login",
        lifecycle_state="active",
        poll_interval_minutes=3,
        allow_browser=True,
        config_version="test",
        effective_at=now,
    )
    plan = EventResearchPlan(
        event_hypothesis="清洁能源船舶订单",
        affected_product_or_service="LNG加注船",
        chain_nodes=[
            ChainNode(
                name="LNG加注船",
                role="供给端",
                evidence_need="核实订单与产能",
                search_terms=["LNG加注船 订单"],
            )
        ],
        company_recall_terms=["LNG加注船"],
        candidate_company_names=["中集安瑞科", "中集集团", "ST无关公司"],
        searches=[
            SearchInstruction(
                query="LNG加注船 订单",
                target_domains=["cninfo.com.cn"],
            )
        ],
    )

    instructions = _relationship_discovery_instructions(
        plan,
        universe=universe,
        sources=(source,),
        operation_budget=3,
    )

    assert len(instructions) == 1
    assert "中集安瑞科" in instructions[0].query
    assert "母公司" in instructions[0].query


def test_targeted_company_search_rejects_other_company_result() -> None:
    instruction = SearchInstruction(
        query="中远海能 LNG运输 订单",
        target_domains=["eastmoney.com"],
        purpose="核实中远海能主营相关性及需求、销售、利润兑现证据",
    )

    assert _result_matches_instruction(
        PublicSearchResult(
            title="中远海控行情页面",
            url="https://quote.eastmoney.com/sh601919.html",
            snippet="中远海控经营数据",
            published_at=None,
        ),
        instruction=instruction,
    ) is False


def test_company_quote_page_is_not_research_evidence() -> None:
    instruction = SearchInstruction(
        query="贵州茅台 飞天茅台 销量 利润 2026",
        target_domains=["eastmoney.com"],
        purpose="核实贵州茅台主营相关性及需求、销售、利润兑现证据",
    )

    assert _result_matches_instruction(
        PublicSearchResult(
            title="贵州茅台(600519)_最新价格_行情_走势图",
            url="https://quote.eastmoney.com/sh600519.html",
            snippet="贵州茅台实时行情、五档盘口和资金流向。",
            published_at=None,
            publication_time_known=False,
        ),
        instruction=instruction,
    ) is False


def test_unknown_time_page_is_not_persisted_as_research_material() -> None:
    now = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)
    request = HuntingRequestRecord(
        request_id="request-unknown-time",
        trigger_method="new_evidence",
        trigger_type="source_event",
        trigger_content='{"discovery_ids":["lead"]}',
        source_url=None,
        as_of_time=now,
        priority=1,
        status="running",
        requested_at=now,
        started_at=now,
        completed_at=None,
        error_summary=None,
    )
    plan = EventResearchPlan(
        event_hypothesis="产品需求增加",
        affected_product_or_service="目标产品",
        chain_nodes=[
            ChainNode(
                name="生产",
                role="供给端",
                evidence_need="核实订单",
                search_terms=["目标产品 订单"],
            )
        ],
        company_recall_terms=["目标产品"],
        searches=[
            SearchInstruction(
                query="目标产品 订单",
                target_domains=["eastmoney.com"],
            )
        ],
    )
    hit = _ResearchHit(
        instruction=plan.searches[0],
        result=PublicSearchResult(
            title="目标产品需求新闻",
            url="https://finance.eastmoney.com/a/unknown.html",
            snippet="页面没有可核实发布时间。",
            published_at=None,
            publication_time_known=False,
        ),
        content_excerpt="目标产品需求增加，但页面没有发布时间。",
        page_read_error=None,
    )

    assert _research_discovery_items(
        (hit,),
        request=request,
        now=now,
        plan=plan,
        source_ids={"eastmoney.com": "eastmoney-discovery"},
        recalled_companies=(),
    ) == ()


def test_gap_search_prefers_current_financial_and_disclosure_sources() -> None:
    now = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)
    plan = EventResearchPlan(
        event_hypothesis="飞天茅台自营渠道提价",
        affected_product_or_service="飞天茅台酒",
        chain_nodes=[
            ChainNode(
                name="茅台酒销售",
                role="需求端",
                evidence_need="当前销量和价格兑现",
                search_terms=["飞天茅台 销量 提价"],
            )
        ],
        company_recall_terms=["茅台酒", "高端白酒"],
        candidate_company_names=["贵州茅台"],
        searches=[
            SearchInstruction(
                query="飞天茅台 提价 销量",
                target_domains=["eastmoney.com"],
            )
        ],
    )

    searches = _fallback_gap_search_plan(
        plan,
        as_of_time=now,
        allowed_domains=frozenset(
            {
                "stats.gov.cn",
                "ndrc.gov.cn",
                "customs.gov.cn",
                "eastmoney.com",
                "cls.cn",
                "cninfo.com.cn",
            }
        ),
    )

    assert searches
    assert all(
        instruction.target_domains[0]
        in {"eastmoney.com", "cls.cn", "cninfo.com.cn"}
        for instruction in searches
    )


def test_company_search_does_not_apply_one_brand_to_unrelated_peers() -> None:
    now = datetime(2026, 7, 27, 20, 0, tzinfo=BEIJING)
    companies = (
        LocalCompanyEvidence(
            code="600519",
            name="贵州茅台",
            universe_version="test",
            availability="available",
            evidence_as_of_date=now.date(),
            published_at=now,
            rules_version="test",
            main_business_name="茅台酒",
            gross_profit_share=0.95,
            revenue_share=0.95,
            source_urls=(),
            ownership_evidence_available=True,
            limitations=(),
        ),
        LocalCompanyEvidence(
            code="002304",
            name="洋河股份",
            universe_version="test",
            availability="available",
            evidence_as_of_date=now.date(),
            published_at=now,
            rules_version="test",
            main_business_name="白酒",
            gross_profit_share=0.9,
            revenue_share=0.9,
            source_urls=(),
            ownership_evidence_available=True,
            limitations=(),
        ),
    )
    sources = (
        RegisteredSource(
            source_id="cninfo",
            source_version=1,
            source_name="巨潮资讯",
            base_url="https://www.cninfo.com.cn/",
            domain="cninfo.com.cn",
            source_tier=1,
            source_type="exchange_disclosure",
            categories=("announcement",),
            access_class="public_no_login",
            lifecycle_state="active",
            poll_interval_minutes=3,
            allow_browser=True,
            config_version="test",
            effective_at=now,
        ),
        RegisteredSource(
            source_id="eastmoney-discovery",
            source_version=1,
            source_name="东方财富",
            base_url="https://finance.eastmoney.com/",
            domain="eastmoney.com",
            source_tier=3,
            source_type="financial_media",
            categories=("company_event",),
            access_class="public_no_login",
            lifecycle_state="active",
            poll_interval_minutes=3,
            allow_browser=True,
            config_version="test",
            effective_at=now,
        ),
    )

    searches = _company_search_instructions(
        companies,
        product="飞天茅台酒",
        sources=sources,
        as_of_time=now,
        operation_budget=4,
    )

    assert len(searches) == 2
    assert "飞天茅台酒" in searches[0].query
    assert "飞天茅台酒" not in searches[1].query
    assert "白酒" in searches[1].query
    assert all("2026年7月" in item.query for item in searches)
    assert all(item.target_domains[0] == "eastmoney.com" for item in searches)


def test_research_budget_rotates_across_questions_before_more_domains() -> None:
    now = datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING)
    calls: list[str] = []

    def search(
        query: str,
        domains: frozenset[str],
        _: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        del domains
        calls.append(query)
        return ()

    _, operations = _collect_research_hits(
        [
            SearchInstruction(
                query="问题一",
                target_domains=["a.test", "b.test"],
            ),
            SearchInstruction(
                query="问题二",
                target_domains=["a.test", "b.test"],
            ),
            SearchInstruction(
                query="问题三",
                target_domains=["a.test", "b.test"],
            ),
        ],
        search=search,
        now=now,
        operation_budget=3,
    )

    assert operations == 3
    assert set(calls) == {
        "site:a.test 问题一",
        "site:a.test 问题二",
        "site:a.test 问题三",
    }


def test_keyword_search_plan_and_results_are_persisted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "search.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING)
    set_hunting_enabled(database, enabled=True, now=now)
    submit_manual_hunt(
        database,
        trigger_type="keyword",
        content="铜矿供应中断",
        now=now,
    )
    request = claim_next_manual_hunt(database, now=now)
    assert request is not None

    def model_transport(
        _: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        del payload
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "searches": [
                            {
                                "query": "铜矿 停产 复产 供应",
                                "target_domains": ["xueqiu.com"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    queries: list[str] = []

    def search(
        query: str,
        domains: frozenset[str],
        _: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        queries.append(query)
        assert domains == frozenset({"xueqiu.com"})
        return (
            PublicSearchResult(
                title="某海外铜矿暂停生产，复产时间仍未确定",
                url="https://xueqiu.com/123/456",
                snippet="矿山发布事故说明。",
                published_at=now,
            ),
        )

    inserted = execute_keyword_search(
        database,
        request=request,
        now=now,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        search=search,
    )
    with sqlite3.connect(database) as connection:
        discovery = connection.execute(
            """
            SELECT source_id, headline
            FROM industry_chain_discovery_items
            """
        ).fetchone()
        search_run = connection.execute(
            """
            SELECT plan_json, results_json, model
            FROM industry_chain_search_runs
            """
        ).fetchone()

    assert len(inserted) == 1
    assert queries == ["site:xueqiu.com 铜矿 停产 复产 供应"]
    assert discovery == (
        "xueqiu-social",
        "某海外铜矿暂停生产，复产时间仍未确定",
    )
    assert search_run is not None
    assert "xueqiu.com" in str(search_run[0])
    assert "矿山发布事故说明" in str(search_run[1])
    assert search_run[2] == "qwen3:14b"


def test_source_event_research_decomposes_chain_searches_and_recalls_company(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-event-research.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 19, 0, tzinfo=BEIJING)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (
                'akshare_sina_daily', '2026-07-25', '000012', '玻璃公司',
                9.8, 10.2, 9.7, 10.0, 9.9, 1.0, 1000, 10000, 100
            )
            """
        )
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (
                'akshare_sina_daily', '2026-07-25', '600001', '点名公司',
                9.8, 10.2, 9.7, 10.0, 9.9, 1.0, 1000, 10000, 100
            )
            """
        )
        connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES (
                '2026-07-25', 'akshare_sina_daily_qfq:2026-07-25',
                '2026-07-25T22:00:00+08:00'
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
                "2026-07-25",
                "personal-fundamental-v1",
                json.dumps(
                    {
                        "main_business_name": "玻璃产品",
                        "main_business_profit_share": 0.88,
                    }
                ),
                '["https://webapi.cninfo.com.cn/api/stock/p_stock2215"]',
                "2026-07-26T10:50:00+08:00",
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
                "2026-07-25",
                "personal-fundamental-v1",
                '["000012"]',
                '["000012"]',
                "2026-07-26T11:00:00+08:00",
                "2026-07-26T11:00:00+08:00",
            ),
        )
    lead = DiscoveryItem(
        discovery_id="cls-news:glass-demand",
        source_id="cls-news",
        external_id="glass-demand",
        security_code=None,
        security_name=None,
        headline="光伏玻璃订单增长，部分规格交付趋紧",
        published_at=now.replace(minute=0),
        collected_at=now,
        source_url="https://www.cls.cn/detail/glass-demand",
        attachment_url=None,
        payload={
            "content": (
                "组件厂排产提升，光伏玻璃部分规格交付趋紧。"
                "新闻同时点名点名公司正在评估相关业务。"
            ),
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (lead,))
    enqueue_source_event_hunt(
        database,
        discovery_ids=(lead.discovery_id,),
        event_types=("supply_demand",),
        priority=1,
        now=now,
    )
    request = claim_next_source_event_hunt(database, now=now)
    assert request is not None

    def model_transport(
        _: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        messages = payload["messages"]
        assert isinstance(messages, list)
        user_message = messages[1]
        assert isinstance(user_message, dict)
        assert "2026-07-26T19:00:00+08:00" in str(user_message["content"])
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "event_hypothesis": "光伏玻璃需求增加并出现交付趋紧",
                        "affected_product_or_service": "光伏玻璃",
                        "chain_nodes": [
                            {
                                "name": "光伏玻璃生产",
                                "role": "core",
                                "evidence_need": "订单、产能利用率和交付周期",
                                "search_terms": ["光伏玻璃", "订单", "交付趋紧"],
                            }
                        ],
                        "company_recall_terms": ["光伏玻璃", "玻璃产品"],
                        "candidate_company_names": [
                            "玻璃公司",
                            "点名公司",
                        ],
                        "searches": [
                            {
                                "query": "光伏玻璃 订单 交付趋紧 2026",
                                "target_domains": ["cninfo.com.cn"],
                                "purpose": "核实供需事件和公司兑现",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            },
        }

    queries: list[str] = []
    read_urls: list[str] = []

    def search(
        query: str,
        domains: frozenset[str],
        _: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        queries.append(query)
        assert domains == frozenset({"cninfo.com.cn"})
        if "玻璃公司" in query:
            return (
                PublicSearchResult(
                    title="玻璃公司光伏玻璃批量供货公告",
                    url="https://www.cninfo.com.cn/new/disclosure/detail?plate=szse&orgId=1",
                    snippet="玻璃公司公告光伏玻璃已经批量供货，本季度开始交付。",
                    published_at=now,
                ),
            )
        return (
            PublicSearchResult(
                title="光伏玻璃订单增加，交付周期延长",
                url="https://www.cninfo.com.cn/new/disclosure/detail?plate=szse&orgId=2",
                snippet="行业订单增加，部分规格交付周期延长。",
                published_at=now,
            ),
        )

    def read_page(item: PublicSearchResult) -> str:
        read_urls.append(item.url)
        if item.url == lead.source_url:
            return (
                "<div class='time'>2026-07-26 18:45</div>"
                "原始新闻正文：组件厂排产提升，光伏玻璃订单增长，"
                "部分规格交付趋紧。"
            )
        return f"已读取正文：{item.snippet}"

    result = execute_source_event_research(
        database,
        request=request,
        now=now,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        search=search,
        read_page=read_page,
        fetch_company_profile=lambda _: pd.DataFrame(),
        fetch_business_segments=lambda _: pd.DataFrame(),
    )
    with sqlite3.connect(database) as connection:
        research_rows = connection.execute(
            """
            SELECT security_code, source_url, published_at, payload_json
            FROM industry_chain_discovery_items
            WHERE json_extract(payload_json, '$.research_parent_request_id') = ?
            ORDER BY security_code IS NULL, security_code
            """,
            (request.request_id,),
        ).fetchall()
        search_run = connection.execute(
            """
            SELECT plan_json, results_json
            FROM industry_chain_search_runs
            WHERE request_id = ?
            """,
            (request.request_id,),
        ).fetchone()

    assert result.company_codes == ("600001", "000012")
    assert result.discovery_ids
    assert lead.source_url in read_urls
    assert any("玻璃公司" in query for query in queries)
    assert any("点名公司" in query for query in queries)
    assert any("当前需求" in query for query in queries)
    assert any(row[0] == "000012" for row in research_rows)
    assert any("已读取正文" in str(row[3]) for row in research_rows)
    original_page_rows = [
        row for row in research_rows if row[1] == lead.source_url
    ]
    assert len(original_page_rows) == 1
    assert original_page_rows[0][2] == "2026-07-26T18:45:00+08:00"
    assert "原始新闻正文" in str(original_page_rows[0][3])
    assert '"publication_time_known":true' in str(
        original_page_rows[0][3]
    )
    assert search_run is not None
    assert "光伏玻璃生产" in str(search_run[0])
    assert "玻璃公司光伏玻璃批量供货公告" in str(search_run[1])
    assert read_untriaged_discovery_ids(
        database,
        as_of_time=now,
        limit=100,
    ) == (
        lead.discovery_id,
    )
