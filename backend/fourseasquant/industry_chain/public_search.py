from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .candidate_expansion import expand_candidate_companies
from .company_evidence import LocalCompanyEvidence
from .company_profile import (
    BusinessFetcher,
    ProfileFetcher,
    collect_cninfo_company_profiles,
)
from .control import HuntingRequestRecord
from .discovery import (
    DiscoveryItem,
    append_discovery_items,
    read_discovery_items,
)
from .entity_matching import company_name_mentioned
from .local_data import LocalIndustryChainData
from .ollama_runtime import OllamaStructuredRuntime, StructuredModelResult
from .public_web import ControlledPublicReader
from .source_registry import RegisteredSource, read_latest_sources
from .universe import EligibleUniverseSnapshot


SEARCH_PROMPT_VERSION = "industry-chain-search-plan-v0.1"
EVENT_RESEARCH_PROMPT_VERSION = "industry-chain-event-research-v0.3"
SEARCHABLE_SOURCE_TYPES = {"financial_media", "social_discovery"}
RESEARCHABLE_SOURCE_TYPES = {
    "exchange_disclosure",
    "financial_media",
    "government",
    "government_statistics",
    "social_discovery",
}
MAX_EVENT_SEARCH_OPERATIONS = 6
MAX_EVENT_PAGE_READS = 6
MAX_ORIGINAL_SOURCE_PAGE_READS = 4
MAX_INITIAL_SEARCH_OPERATIONS = 2
MAX_INITIAL_PAGE_READS = 2
MAX_GAP_SEARCH_OPERATIONS = 2
MAX_GAP_PAGE_READS = 2
MAX_CANDIDATE_DISCOVERY_SEARCH_OPERATIONS = 6
MAX_CANDIDATE_DISCOVERY_PAGE_READS = 4
MAX_COMPANY_CLOSURE_SEARCH_OPERATIONS = 24
MAX_COMPANY_CLOSURE_PAGE_READS = 12
MAX_RESEARCH_RESULTS = 40
MAX_RECALLED_COMPANIES = 24
MAX_RESEARCH_EXCERPT_CHARS = 5_000
MAX_RESEARCH_RESULT_AGE = timedelta(hours=72)
YEAR_PATTERN = re.compile(r"\b20\d{2}\b")
INELIGIBLE_MODEL_NAME = re.compile(r"^(?:\*?ST|退市)", re.IGNORECASE)
TARGETED_RESEARCH_PURPOSE = re.compile(
    r"^核实(.+?)(?:主营相关性|与A股上市公司的控股、持股或并表关系)"
)
LOW_VALUE_RESULT_TITLE = re.compile(
    r"(最新价格.{0,8}行情.{0,8}走势图|股票行情|实时行情|"
    r"五档盘口|资金流向|行情中心)"
)
LOW_VALUE_RESULT_HOSTS = {
    "quote.eastmoney.com",
    "q.10jqka.com.cn",
    "stockpage.10jqka.com.cn",
}
URL_DATE_PATTERN = re.compile(
    r"(20\d{2})(?:[-_/]?)(\d{2})(?:[-_/]?)(\d{2})"
)
ISO_DATE_PATTERN = re.compile(
    r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"
)
CHINESE_DATE_PATTERN = re.compile(
    r"\b(20\d{2})年(\d{1,2})月(\d{1,2})日"
)
ISO_DATETIME_PATTERN = re.compile(
    r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})"
    r"[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?"
)
CHINESE_DATETIME_PATTERN = re.compile(
    r"\b(20\d{2})年(\d{1,2})月(\d{1,2})日"
    r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"
)


class SearchInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=100)
    target_domains: list[str] = Field(min_length=1, max_length=3)
    purpose: str = Field(default="", max_length=200)


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    searches: list[SearchInstruction] = Field(min_length=1, max_length=6)


class ChainNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=40)
    evidence_need: str = Field(min_length=1, max_length=200)
    search_terms: list[str] = Field(min_length=1, max_length=8)


class EventResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_hypothesis: str = Field(min_length=1, max_length=300)
    affected_product_or_service: str = Field(min_length=1, max_length=120)
    chain_nodes: list[ChainNode] = Field(min_length=1, max_length=8)
    company_recall_terms: list[str] = Field(min_length=1, max_length=16)
    candidate_company_names: list[str] = Field(
        default_factory=list,
        max_length=24,
    )
    searches: list[SearchInstruction] = Field(min_length=1, max_length=6)


@dataclass(frozen=True)
class SearchPlanResult:
    plan: SearchPlan
    model_result: StructuredModelResult


@dataclass(frozen=True)
class EventResearchPlanResult:
    plan: EventResearchPlan
    model_result: StructuredModelResult


@dataclass(frozen=True)
class PublicSearchResult:
    title: str
    url: str
    snippet: str
    published_at: datetime | None
    publication_time_known: bool | None = None


@dataclass(frozen=True)
class SourceEventResearchResult:
    plan: EventResearchPlan
    discovery_ids: tuple[str, ...]
    company_codes: tuple[str, ...]
    search_operations: int
    page_reads: int


SearchFunction = Callable[
    [str, frozenset[str], datetime],
    tuple[PublicSearchResult, ...],
]
PageReadFunction = Callable[[PublicSearchResult], str]


@dataclass(frozen=True)
class _ResearchHit:
    instruction: SearchInstruction
    result: PublicSearchResult
    content_excerpt: str
    page_read_error: str | None


SYSTEM_PROMPT = """
你是产业链供需搜索规划 Agent。你只负责生成检索词和选择目标域名，不负责最终选股。
围绕用户输入寻找真实的供应收缩、需求增长、价格、库存、产能、重大订单和主营受益线索。
政策只作为观察线索。不要加入股价、涨停、技术走势、估值或荐股词。
优先使用能区分事件、产品、地区和上下游的具体检索词；必要时包含中英文同义词。
目标域名只能从用户提供的 allowed_domains 中逐字选择，不得生成其他域名。
""".strip()

EVENT_RESEARCH_SYSTEM_PROMPT = """
你是产业链供需研究规划 Agent，只负责把已发现线索拆成可核查的产业链问题和公开检索计划，不负责最终选股。
必须先区分受影响产品、供给端、核心生产或服务节点、需求端、替代品和公司兑现证据。
检索词要用于核实真实供需、价格、库存、订单、产能、交付周期、主营产品和批量供货；不要搜索股价、涨停、技术走势、估值或荐股内容。
政策只能作为观察线索，必须继续寻找已经发生的供需事实。
单一公司、品牌或具体商品的当期价格、销量、订单和客户证据，应优先选择交易所披露或财经媒体；不得默认交给统计局、发改委、海关或工信部。
目标域名只能从 allowed_domains 中逐字选择。
current_time 是本次研究时间；除非输入事件本身明确涉及历史年份，否则检索词中的年份必须使用 current_year。
company_recall_terms 只保留可与公司主营业务匹配的产品、材料、设备或服务名称，不要写公司名、“需求增长”“产业链”“利好”等泛词。
每个受影响产品至少给出两个独立的主营业务同义词或上位类别词。例如“储能大电芯”应同时给出“储能电芯”“储能电池”“锂离子电池”，不要只返回带规格的长词。
candidate_company_names 用于提出需要进一步核验的沪深主板公司，不代表入选。应尽量列全你已知的直接生产商、核心供应商或明确承接需求的公司，最多24家；不得加入仅有概念叙事但没有相关主营可能性的公司。系统会再核验股票范围、主营占比和当期兑现证据。
""".strip()

EVIDENCE_GAP_SYSTEM_PROMPT = """
你是产业链证据缺口补搜 Agent。第一轮搜索已经结束，你必须阅读已有结果，再生成不重复的补充检索。
补搜至少覆盖以下尚未被真实证据证明的环节：当前新增需求、供给或库存缺口、价格或交付变化、公司主营承接、订单或销量兑现、利润传导。
扩产、投产和技术升级本身不是需求或利润证据；必须补搜客户、订单、销量、利用率、售价、收入或利润。
板块上涨、震荡反弹、概念异动和研报判断不是证据，要追查其引用的原始供需事实。
已经结束的上半年、季度或历史统计必须补搜当前月份仍在持续的新证据。
单一公司、品牌或具体商品的销量、价格、客户和利润，不得默认去统计局、发改委、海关或工信部检索；优先使用交易所披露和财经媒体。
目标域名只能从 allowed_domains 中逐字选择，不得加入股价、涨停、估值或荐股词。
""".strip()


def plan_public_search(
    input_text: str,
    *,
    allowed_domains: frozenset[str],
    runtime: OllamaStructuredRuntime,
    as_of_time: datetime | None = None,
) -> SearchPlanResult:
    reference_time = as_of_time
    result = runtime.complete(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "input": input_text,
                "allowed_domains": sorted(allowed_domains),
                "current_time": (
                    reference_time.isoformat()
                    if reference_time is not None
                    else None
                ),
                "current_year": (
                    reference_time.year if reference_time is not None else None
                ),
            },
            ensure_ascii=False,
        ),
        output_schema=SearchPlan.model_json_schema(),
    )
    plan = SearchPlan.model_validate(result.payload)
    if reference_time is not None:
        plan = SearchPlan(
            searches=[
                instruction.model_copy(
                    update={
                        "query": _normalize_query_years(
                            instruction.query,
                            reference_time.year,
                        )
                    }
                )
                for instruction in plan.searches
            ]
        )
    returned_domains = {
        domain
        for instruction in plan.searches
        for domain in instruction.target_domains
    }
    unknown = returned_domains - allowed_domains
    if unknown:
        raise ValueError(f"搜索计划包含未授权域名：{sorted(unknown)}")
    return SearchPlanResult(plan=plan, model_result=result)


def plan_source_event_research(
    items: tuple[DiscoveryItem, ...],
    *,
    as_of_time: datetime,
    allowed_domains: frozenset[str],
    runtime: OllamaStructuredRuntime,
) -> EventResearchPlanResult:
    if not items:
        raise ValueError("来源事件研究不能为空")
    if not allowed_domains:
        raise ValueError("没有可用于产业链研究的公开来源域名")
    model_result = runtime.complete(
        system_prompt=EVENT_RESEARCH_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "current_time": as_of_time.isoformat(),
                "current_year": as_of_time.year,
                "allowed_domains": sorted(allowed_domains),
                "discoveries": [
                    {
                        "discovery_id": item.discovery_id,
                        "source_id": item.source_id,
                        "headline": item.headline,
                        "published_at": item.published_at.isoformat(),
                        "content_excerpt": _discovery_excerpt(item)[:2_000],
                    }
                    for item in items
                ],
            },
            ensure_ascii=False,
        ),
        output_schema=EventResearchPlan.model_json_schema(),
    )
    try:
        raw_plan = EventResearchPlan.model_validate(model_result.payload)
    except ValidationError:
        raw_plan = _fallback_event_research_plan(
            items,
            as_of_time=as_of_time,
            allowed_domains=allowed_domains,
        )
    plan = _normalize_event_research_plan(
        raw_plan,
        as_of_time=as_of_time,
        allowed_domains=allowed_domains,
    )
    return EventResearchPlanResult(plan=plan, model_result=model_result)


def plan_evidence_gap_search(
    plan: EventResearchPlan,
    hits: tuple[_ResearchHit, ...],
    *,
    as_of_time: datetime,
    allowed_domains: frozenset[str],
    runtime: OllamaStructuredRuntime,
) -> list[SearchInstruction]:
    fallback = _fallback_gap_search_plan(
        plan,
        as_of_time=as_of_time,
        allowed_domains=allowed_domains,
    )
    try:
        model_result = runtime.complete(
            system_prompt=EVIDENCE_GAP_SYSTEM_PROMPT,
            user_prompt=json.dumps(
                {
                    "current_time": as_of_time.isoformat(),
                    "current_year": as_of_time.year,
                    "allowed_domains": sorted(allowed_domains),
                    "event_research": plan.model_dump(),
                    "first_round_results": [
                        {
                            "title": hit.result.title,
                            "url": hit.result.url,
                            "snippet": hit.result.snippet,
                            "content_excerpt": hit.content_excerpt[:1500],
                            "query": hit.instruction.query,
                            "purpose": hit.instruction.purpose,
                        }
                        for hit in hits[:12]
                    ],
                },
                ensure_ascii=False,
            ),
            output_schema=SearchPlan.model_json_schema(),
        )
        raw = SearchPlan.model_validate(model_result.payload)
    except Exception:
        return fallback
    normalized = [
        instruction.model_copy(
            update={
                "query": _normalize_query_years(
                    instruction.query,
                    as_of_time.year,
                ),
                "target_domains": list(
                    dict.fromkeys(
                        domain
                        for domain in instruction.target_domains
                        if domain in allowed_domains
                    )
                ),
            }
        )
        for instruction in raw.searches
    ]
    accepted = [
        instruction
        for instruction in normalized
        if instruction.target_domains
    ]
    combined: list[SearchInstruction] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for instruction in (*fallback, *accepted):
        key = (
            instruction.query,
            tuple(instruction.target_domains),
        )
        if key in seen:
            continue
        seen.add(key)
        combined.append(instruction)
    return combined


class BingRssSearch:
    def __init__(self, path: Path) -> None:
        self._path = path

    def search(
        self,
        query: str,
        allowed_domains: frozenset[str],
        now: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        del now
        url = f"https://www.bing.com/search?{urlencode({'q': query, 'format': 'rss'})}"
        with ControlledPublicReader(
            self._path,
            maximum_bytes=2 * 1024 * 1024,
            request_timeout_seconds=8.0,
        ) as reader:
            document = reader.read(
                url,
                allow_unregistered_public=True,
            )
        root = ElementTree.fromstring(document.content)
        results: list[PublicSearchResult] = []
        for item in root.findall("./channel/item"):
            result_url = (item.findtext("link") or "").strip()
            hostname = (urlparse(result_url).hostname or "").lower()
            if not any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in allowed_domains
            ):
                continue
            published_text = (item.findtext("pubDate") or "").strip()
            published_at = (
                parsedate_to_datetime(published_text)
                if published_text
                else None
            )
            results.append(
                PublicSearchResult(
                    title=(item.findtext("title") or "").strip()[:300],
                    url=result_url,
                    snippet=(item.findtext("description") or "").strip()[:1000],
                    published_at=published_at,
                )
            )
            if len(results) >= 10:
                break
        return tuple(results)


@dataclass(frozen=True)
class _HtmlSearchRow:
    title: str
    url: str
    snippet: str


class _DuckDuckGoResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_HtmlSearchRow] = []
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._finish_row()
            self._href = attributes.get("href")
            self._capture_title = True
            self._title_parts = []
            self._snippet_parts = []
        elif "result__snippet" in classes and self._href is not None:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if self._capture_snippet and tag in {"a", "div", "span"}:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._capture_title:
            self._title_parts.append(normalized)
        elif self._capture_snippet:
            self._snippet_parts.append(normalized)

    def close(self) -> None:
        super().close()
        self._finish_row()

    def _finish_row(self) -> None:
        if self._href is None:
            return
        title = " ".join(self._title_parts).strip()
        url = _duckduckgo_target_url(self._href)
        if title and url:
            self.rows.append(
                _HtmlSearchRow(
                    title=title,
                    url=url,
                    snippet=" ".join(self._snippet_parts).strip(),
                )
            )
        self._href = None
        self._title_parts = []
        self._snippet_parts = []
        self._capture_title = False
        self._capture_snippet = False


class _BingHtmlResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_HtmlSearchRow] = []
        self._result_depth = 0
        self._in_heading = False
        self._capture_title = False
        self._capture_snippet = False
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and "b_algo" in classes:
            self._finish_row()
            self._result_depth = 1
            return
        if self._result_depth == 0:
            return
        self._result_depth += 1
        if tag == "h2":
            self._in_heading = True
        elif tag == "a" and self._in_heading and self._href is None:
            self._href = attributes.get("href")
            self._capture_title = True
        elif tag == "p":
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self._result_depth == 0:
            return
        if tag == "a" and self._capture_title:
            self._capture_title = False
        elif tag == "p" and self._capture_snippet:
            self._capture_snippet = False
        elif tag == "h2":
            self._in_heading = False
        self._result_depth -= 1
        if self._result_depth == 0:
            self._finish_row()

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._capture_title:
            self._title_parts.append(normalized)
        elif self._capture_snippet:
            self._snippet_parts.append(normalized)

    def close(self) -> None:
        super().close()
        self._finish_row()

    def _finish_row(self) -> None:
        title = " ".join(self._title_parts).strip()
        if self._href and title:
            self.rows.append(
                _HtmlSearchRow(
                    title=title,
                    url=self._href,
                    snippet=" ".join(self._snippet_parts).strip(),
                )
            )
        self._result_depth = 0
        self._in_heading = False
        self._capture_title = False
        self._capture_snippet = False
        self._href = None
        self._title_parts = []
        self._snippet_parts = []


class _SoHtmlResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_HtmlSearchRow] = []
        self._result_depth = 0
        self._in_heading = False
        self._capture_title = False
        self._capture_snippet = False
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "li" and "res-list" in classes:
            self._finish_row()
            self._result_depth = 1
            return
        if self._result_depth == 0:
            return
        self._result_depth += 1
        if tag == "h3":
            self._in_heading = True
        elif tag == "a" and self._in_heading and self._href is None:
            self._href = attributes.get("data-mdurl") or attributes.get(
                "href"
            )
            self._capture_title = True
        elif (
            tag in {"p", "span"}
            and classes.intersection({"res-desc", "res-list-summary"})
        ):
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self._result_depth == 0:
            return
        if tag == "a" and self._capture_title:
            self._capture_title = False
        elif tag in {"p", "span"} and self._capture_snippet:
            self._capture_snippet = False
        elif tag == "h3":
            self._in_heading = False
        self._result_depth -= 1
        if self._result_depth == 0:
            self._finish_row()

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._capture_title:
            self._title_parts.append(normalized)
        elif self._capture_snippet:
            self._snippet_parts.append(normalized)

    def close(self) -> None:
        super().close()
        self._finish_row()

    def _finish_row(self) -> None:
        title = " ".join(self._title_parts).strip()
        if self._href and title:
            self.rows.append(
                _HtmlSearchRow(
                    title=title,
                    url=self._href,
                    snippet=" ".join(self._snippet_parts).strip(),
                )
            )
        self._result_depth = 0
        self._in_heading = False
        self._capture_title = False
        self._capture_snippet = False
        self._href = None
        self._title_parts = []
        self._snippet_parts = []


class _PublicationTimeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self._leading_text_parts: list[str] = []
        self._leading_text_chars = 0
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif tag == "meta":
            key = (
                attributes.get("property")
                or attributes.get("name")
                or attributes.get("itemprop")
                or ""
            ).lower()
            if key in {
                "article:published_time",
                "publishdate",
                "pubdate",
                "datepublished",
                "og:published_time",
            }:
                value = attributes.get("content")
                if value:
                    self.values.append(value)
        elif tag == "time":
            value = attributes.get("datetime")
            if value:
                self.values.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0 or self._leading_text_chars >= 6_000:
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        remaining = 6_000 - self._leading_text_chars
        value = normalized[:remaining]
        self._leading_text_parts.append(value)
        self._leading_text_chars += len(value)

    def close(self) -> None:
        super().close()
        if self._leading_text_parts:
            self.values.append(" ".join(self._leading_text_parts))


class DuckDuckGoHtmlSearch:
    """无需密钥的公开 HTML 搜索；结果仍受登记域名白名单约束。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def search(
        self,
        query: str,
        allowed_domains: frozenset[str],
        now: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        url = (
            "https://html.duckduckgo.com/html/?"
            f"{urlencode({'q': query})}"
        )
        with ControlledPublicReader(
            self._path,
            maximum_bytes=2 * 1024 * 1024,
            request_timeout_seconds=8.0,
        ) as reader:
            document = reader.read(
                url,
                allow_unregistered_public=True,
            )
        parser = _DuckDuckGoResultParser()
        parser.feed(document.content.decode("utf-8", errors="replace"))
        parser.close()
        results: list[PublicSearchResult] = []
        for row in parser.rows:
            hostname = (urlparse(row.url).hostname or "").lower()
            if not any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in allowed_domains
            ):
                continue
            published_at = _search_result_date(
                f"{row.title} {row.snippet}",
                reference=now,
            ) or _url_date(row.url, reference=now)
            results.append(
                PublicSearchResult(
                    title=row.title[:300],
                    url=row.url,
                    snippet=row.snippet[:1_000],
                    published_at=published_at,
                    publication_time_known=False,
                )
            )
            if len(results) >= 10:
                break
        return tuple(results)


class BingHtmlSearch:
    """无需密钥的必应 HTML 搜索；解析后再次强制目标域名白名单。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def search(
        self,
        query: str,
        allowed_domains: frozenset[str],
        now: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        url = f"https://www.bing.com/search?{urlencode({'q': query})}"
        with ControlledPublicReader(
            self._path,
            maximum_bytes=2 * 1024 * 1024,
            request_timeout_seconds=8.0,
        ) as reader:
            document = reader.read(
                url,
                allow_unregistered_public=True,
            )
        parser = _BingHtmlResultParser()
        parser.feed(document.content.decode("utf-8", errors="replace"))
        parser.close()
        results: list[PublicSearchResult] = []
        for row in parser.rows:
            hostname = (urlparse(row.url).hostname or "").lower()
            if not any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in allowed_domains
            ):
                continue
            published_at = _search_result_date(
                f"{row.title} {row.snippet}",
                reference=now,
            ) or _url_date(row.url, reference=now)
            results.append(
                PublicSearchResult(
                    title=row.title[:300],
                    url=row.url,
                    snippet=row.snippet[:1_000],
                    published_at=published_at,
                    publication_time_known=False,
                )
            )
            if len(results) >= 10:
                break
        return tuple(results)


class SoHtmlSearch:
    """无需密钥的 360 HTML 搜索；优先使用结果携带的原始网址。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def search(
        self,
        query: str,
        allowed_domains: frozenset[str],
        now: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        url = f"https://www.so.com/s?{urlencode({'q': query})}"
        with ControlledPublicReader(
            self._path,
            maximum_bytes=2 * 1024 * 1024,
            request_timeout_seconds=8.0,
        ) as reader:
            document = reader.read(
                url,
                allow_unregistered_public=True,
            )
        parser = _SoHtmlResultParser()
        parser.feed(document.content.decode("utf-8", errors="replace"))
        parser.close()
        results: list[PublicSearchResult] = []
        for row in parser.rows:
            hostname = (urlparse(row.url).hostname or "").lower()
            if not any(
                hostname == domain or hostname.endswith(f".{domain}")
                for domain in allowed_domains
            ):
                continue
            published_at = _search_result_date(
                f"{row.title} {row.snippet}",
                reference=now,
            ) or _url_date(row.url, reference=now)
            results.append(
                PublicSearchResult(
                    title=row.title[:300],
                    url=row.url,
                    snippet=row.snippet[:1_000],
                    published_at=published_at,
                    publication_time_known=False,
                )
            )
            if len(results) >= 10:
                break
        return tuple(results)


class FreePublicSearch:
    """组合无需密钥的公开搜索，并始终执行结果域名白名单。"""

    def __init__(self, path: Path) -> None:
        self._so_html = SoHtmlSearch(path)
        self._bing_html = BingHtmlSearch(path)
        self._duckduckgo = DuckDuckGoHtmlSearch(path)
        self._bing_rss = BingRssSearch(path)

    def search(
        self,
        query: str,
        allowed_domains: frozenset[str],
        now: datetime,
    ) -> tuple[PublicSearchResult, ...]:
        try:
            results = self._so_html.search(
                query,
                allowed_domains,
                now,
            )
            if results:
                return results
        except Exception:
            pass
        try:
            results = self._bing_html.search(
                query,
                allowed_domains,
                now,
            )
            if results:
                return results
        except Exception:
            pass
        try:
            results = self._duckduckgo.search(
                query,
                allowed_domains,
                now,
            )
            if results:
                return results
        except Exception:
            pass
        return self._bing_rss.search(query, allowed_domains, now)


def execute_keyword_search(
    path: Path,
    *,
    request: HuntingRequestRecord,
    now: datetime,
    runtime: OllamaStructuredRuntime,
    search: SearchFunction | None = None,
) -> tuple[str, ...]:
    if request.trigger_type != "keyword":
        raise ValueError("公开搜索执行器只处理关键词请求")
    sources = tuple(
        source
        for source in read_latest_sources(path)
        if source.lifecycle_state == "active"
        and source.source_type in SEARCHABLE_SOURCE_TYPES
    )
    allowed_domains = frozenset(source.domain for source in sources)
    source_ids = {source.domain: source.source_id for source in sources}
    started_at = datetime.now(now.tzinfo)
    planned = plan_public_search(
        request.trigger_content,
        allowed_domains=allowed_domains,
        runtime=runtime,
        as_of_time=now,
    )
    search_function = search or FreePublicSearch(path).search
    all_results: list[tuple[SearchInstruction, PublicSearchResult]] = []
    for instruction in planned.plan.searches:
        for domain in instruction.target_domains:
            query = f"site:{domain} {instruction.query}"
            results = search_function(query, frozenset({domain}), now)
            all_results.extend((instruction, result) for result in results)
    deduplicated: dict[str, tuple[SearchInstruction, PublicSearchResult]] = {}
    for instruction, result in all_results:
        deduplicated.setdefault(result.url, (instruction, result))
    items: list[DiscoveryItem] = []
    for instruction, result in tuple(deduplicated.values())[:100]:
        hostname = (urlparse(result.url).hostname or "").lower()
        matched_domain = next(
            (
                candidate
                for candidate in allowed_domains
                if hostname == candidate or hostname.endswith(f".{candidate}")
            ),
            None,
        )
        if matched_domain is None or not result.title:
            continue
        external_id = hashlib.sha256(result.url.encode()).hexdigest()
        items.append(
            DiscoveryItem(
                discovery_id=f"{source_ids[matched_domain]}:{external_id[:32]}",
                source_id=source_ids[matched_domain],
                external_id=external_id,
                security_code=None,
                security_name=None,
                headline=result.title,
                published_at=result.published_at or now,
                collected_at=now,
                source_url=result.url,
                attachment_url=None,
                payload={
                    "snippet": result.snippet,
                    "search_query": instruction.query,
                    "search_intermediary": "free_public_search",
                    "published_at_known": result.published_at is not None,
                },
            )
        )
    inserted = append_discovery_items(path, tuple(items)).inserted_ids
    completed_at = datetime.now(now.tzinfo)
    _save_search_run(
        path,
        request=request,
        planned=planned,
        results=tuple(result for _, result in deduplicated.values()),
        started_at=started_at,
        completed_at=completed_at,
    )
    return inserted


def execute_source_event_research(
    path: Path,
    *,
    request: HuntingRequestRecord,
    now: datetime,
    runtime: OllamaStructuredRuntime,
    search: SearchFunction | None = None,
    read_page: PageReadFunction | None = None,
    fetch_company_profile: ProfileFetcher | None = None,
    fetch_business_segments: BusinessFetcher | None = None,
) -> SourceEventResearchResult:
    if request.trigger_type != "source_event":
        raise ValueError("自动产业链研究只处理来源事件")
    original_ids = _source_event_discovery_ids(request)
    original_items = read_discovery_items(path, original_ids)
    if not original_items:
        raise ValueError("来源事件没有可研究的发现记录")
    started_at = datetime.now(now.tzinfo)
    original_hits = _original_event_hits(original_items)
    original_hits, original_page_reads = _read_research_pages(
        path,
        original_hits,
        read_page=read_page,
        page_budget=MAX_ORIGINAL_SOURCE_PAGE_READS,
        reference_time=request.as_of_time,
    )
    planning_items = _merge_original_page_content(
        original_items,
        original_hits,
    )
    sources = tuple(
        source
        for source in read_latest_sources(path)
        if source.lifecycle_state == "active"
        and source.allow_browser
        and source.source_type in RESEARCHABLE_SOURCE_TYPES
    )
    allowed_domains = frozenset(source.domain for source in sources)
    source_ids = {source.domain: source.source_id for source in sources}
    planned = plan_source_event_research(
        planning_items,
        as_of_time=request.as_of_time,
        allowed_domains=allowed_domains,
        runtime=runtime,
    )
    local_data = LocalIndustryChainData(path)
    universe = local_data.eligible_universe(as_of_time=request.as_of_time)
    search_function = search or FreePublicSearch(path).search
    initial_hits, search_operations = _collect_research_hits(
        planned.plan.searches,
        search=search_function,
        now=request.as_of_time,
        operation_budget=MAX_INITIAL_SEARCH_OPERATIONS,
    )
    initial_hits, page_reads = _read_research_pages(
        path,
        initial_hits,
        read_page=read_page,
        page_budget=MAX_INITIAL_PAGE_READS,
        reference_time=request.as_of_time,
    )
    gap_instructions = plan_evidence_gap_search(
        planned.plan,
        initial_hits,
        as_of_time=request.as_of_time,
        allowed_domains=allowed_domains,
        runtime=runtime,
    )
    gap_hits, gap_operations = _collect_research_hits(
        gap_instructions,
        search=search_function,
        now=request.as_of_time,
        operation_budget=min(
            MAX_GAP_SEARCH_OPERATIONS,
            MAX_EVENT_SEARCH_OPERATIONS - search_operations,
        ),
    )
    gap_hits, gap_page_reads = _read_research_pages(
        path,
        gap_hits,
        read_page=read_page,
        page_budget=min(
            MAX_GAP_PAGE_READS,
            MAX_EVENT_PAGE_READS - page_reads,
        ),
        reference_time=request.as_of_time,
    )
    event_hits = _deduplicate_hits(
        (*original_hits, *initial_hits, *gap_hits)
    )

    relationship_instructions = _relationship_discovery_instructions(
        planned.plan,
        universe=universe,
        sources=sources,
        operation_budget=min(
            3,
            MAX_CANDIDATE_DISCOVERY_SEARCH_OPERATIONS,
        ),
    )
    candidate_instructions = _candidate_discovery_instructions(
        planned.plan,
        sources=sources,
        operation_budget=(
            MAX_CANDIDATE_DISCOVERY_SEARCH_OPERATIONS
            - len(relationship_instructions)
        ),
    )
    candidate_instructions = [
        *relationship_instructions,
        *candidate_instructions,
    ]
    candidate_hits, candidate_operations = _collect_research_hits(
        candidate_instructions,
        search=search_function,
        now=request.as_of_time,
        operation_budget=MAX_CANDIDATE_DISCOVERY_SEARCH_OPERATIONS,
    )
    candidate_hits, candidate_page_reads = _read_research_pages(
        path,
        candidate_hits,
        read_page=read_page,
        page_budget=MAX_CANDIDATE_DISCOVERY_PAGE_READS,
        reference_time=request.as_of_time,
    )
    research_hits = _deduplicate_hits((*event_hits, *candidate_hits))

    original_corpus = [
        " ".join((item.headline, _discovery_excerpt(item)))
        for item in original_items
    ]
    expanded_corpus = [
        " ".join(
            (
                hit.result.title,
                hit.result.snippet,
                hit.content_excerpt,
            )
        )
        for hit in research_hits
        if _research_hit_is_persistable(hit)
    ]
    eligible_names = {item.name for item in universe.securities}
    proposed_listed_names = [
        name
        for name in planned.plan.candidate_company_names
        if name in eligible_names
    ]
    expansion = expand_candidate_companies(
        local_data=local_data,
        universe=universe,
        original_corpus=original_corpus,
        expanded_corpus=[
            *expanded_corpus,
            " ".join(proposed_listed_names),
        ],
        recall_terms=_research_recall_terms(planned.plan),
        as_of_time=request.as_of_time,
        limit=MAX_RECALLED_COMPANIES,
    )
    recalled = expansion.companies
    company_profile_items = collect_cninfo_company_profiles(
        recalled,
        as_of_time=request.as_of_time,
        observed_at=now,
        fetch_profile=fetch_company_profile,
        fetch_business_segments=fetch_business_segments,
    )
    append_discovery_items(path, company_profile_items)
    company_instructions = _company_search_instructions(
        recalled,
        product=planned.plan.affected_product_or_service,
        sources=sources,
        as_of_time=request.as_of_time,
        operation_budget=MAX_COMPANY_CLOSURE_SEARCH_OPERATIONS,
    )
    company_hits, company_operations = _collect_research_hits(
        company_instructions,
        search=search_function,
        now=request.as_of_time,
        operation_budget=MAX_COMPANY_CLOSURE_SEARCH_OPERATIONS,
    )
    company_hits, company_page_reads = _read_research_pages(
        path,
        company_hits,
        read_page=read_page,
        page_budget=MAX_COMPANY_CLOSURE_PAGE_READS,
        reference_time=request.as_of_time,
    )
    all_hits = _deduplicate_hits((*research_hits, *company_hits))
    research_items = _research_discovery_items(
        all_hits,
        request=request,
        now=now,
        plan=planned.plan,
        source_ids=source_ids,
        recalled_companies=recalled,
    )
    append_discovery_items(path, (*research_items, *company_profile_items))
    total_search_operations = (
        search_operations
        + gap_operations
        + candidate_operations
        + company_operations
    )
    total_page_reads = (
        original_page_reads
        + page_reads
        + gap_page_reads
        + candidate_page_reads
        + company_page_reads
    )
    _save_source_event_search_run(
        path,
        request=request,
        planned=planned,
        gap_instructions=gap_instructions,
        candidate_instructions=candidate_instructions,
        company_instructions=company_instructions,
        recalled_companies=recalled,
        hits=all_hits,
        search_operations=total_search_operations,
        page_reads=total_page_reads,
        original_page_reads=original_page_reads,
        started_at=started_at,
        completed_at=datetime.now(now.tzinfo),
    )
    return SourceEventResearchResult(
        plan=planned.plan,
        discovery_ids=tuple(
            item.discovery_id
            for item in (*research_items, *company_profile_items)
        ),
        company_codes=tuple(company.code for company in recalled),
        search_operations=total_search_operations,
        page_reads=total_page_reads,
    )


def _original_event_hits(
    items: tuple[DiscoveryItem, ...],
) -> tuple[_ResearchHit, ...]:
    hits: list[_ResearchHit] = []
    for item in items:
        hostname = (urlparse(item.source_url).hostname or "").lower()
        if not hostname:
            continue
        publication_time_known = (
            item.payload.get("publication_time_known") is True
        )
        hits.append(
            _ResearchHit(
                instruction=SearchInstruction(
                    query=item.headline[:100],
                    target_domains=[hostname],
                    purpose="读取原始事件正文并核实发布时间",
                ),
                result=PublicSearchResult(
                    title=item.headline,
                    url=item.source_url,
                    snippet=_discovery_excerpt(item)[:1_000],
                    published_at=(
                        item.published_at
                        if publication_time_known
                        else None
                    ),
                    publication_time_known=publication_time_known,
                ),
                content_excerpt="",
                page_read_error=None,
            )
        )
    return tuple(hits)


def _merge_original_page_content(
    items: tuple[DiscoveryItem, ...],
    hits: tuple[_ResearchHit, ...],
) -> tuple[DiscoveryItem, ...]:
    hit_by_url = {hit.result.url: hit for hit in hits}
    merged: list[DiscoveryItem] = []
    for item in items:
        hit = hit_by_url.get(item.source_url)
        if hit is None:
            merged.append(item)
            continue
        payload = dict(item.payload)
        if hit.content_excerpt:
            payload["content"] = hit.content_excerpt
        if hit.result.publication_time_known is not None:
            payload["publication_time_known"] = (
                hit.result.publication_time_known
            )
        payload["page_read_succeeded"] = bool(hit.content_excerpt)
        payload["page_read_error"] = hit.page_read_error
        merged.append(
            replace(
                item,
                published_at=hit.result.published_at or item.published_at,
                payload=payload,
            )
        )
    return tuple(merged)


def _collect_research_hits(
    instructions: list[SearchInstruction],
    *,
    search: SearchFunction,
    now: datetime,
    operation_budget: int,
) -> tuple[tuple[_ResearchHit, ...], int]:
    if operation_budget <= 0:
        return (), 0
    hits: list[_ResearchHit] = []
    tasks: list[tuple[SearchInstruction, str]] = []
    maximum_domains = max(
        (len(instruction.target_domains) for instruction in instructions),
        default=0,
    )
    for domain_index in range(maximum_domains):
        for instruction in instructions:
            if domain_index >= len(instruction.target_domains):
                continue
            domain = instruction.target_domains[domain_index]
            if len(tasks) >= operation_budget:
                break
            tasks.append((instruction, domain))
        if len(tasks) >= operation_budget:
            break

    operations = 0
    for instruction, domain in tasks:
        operations += 1
        query = f"site:{domain} {instruction.query}"
        try:
            results = search(query, frozenset({domain}), now)
        except Exception:
            continue
        for result in results:
            result = _with_inferred_publication_time(
                result,
                reference=now,
            )
            if not _result_matches_instruction(
                result,
                instruction=instruction,
            ):
                continue
            if not _is_fresh_research_result(
                result,
                as_of_time=now,
            ):
                continue
            hits.append(
                _ResearchHit(
                    instruction=instruction,
                    result=result,
                    content_excerpt="",
                    page_read_error=None,
                )
            )
            if len(hits) >= MAX_RESEARCH_RESULTS:
                return _deduplicate_hits(tuple(hits)), operations
    return _deduplicate_hits(tuple(hits)), operations


def _read_research_pages(
    path: Path,
    hits: tuple[_ResearchHit, ...],
    *,
    read_page: PageReadFunction | None,
    page_budget: int,
    reference_time: datetime,
) -> tuple[tuple[_ResearchHit, ...], int]:
    if not hits or page_budget <= 0:
        return hits, 0
    page_reads = 0
    enriched: list[_ResearchHit] = []
    reader = (
        None
        if read_page is not None
        else ControlledPublicReader(path, maximum_bytes=2 * 1024 * 1024)
    )
    try:
        for hit in hits:
            if page_reads >= page_budget:
                enriched.append(hit)
                continue
            page_reads += 1
            try:
                if read_page is not None:
                    content = read_page(hit.result)
                    enriched_result = _enrich_publication_time_from_html(
                        hit.result,
                        content=content.encode("utf-8"),
                        reference=reference_time,
                    )
                elif reader is not None:
                    document = reader.read(hit.result.url)
                    content = document.text
                    enriched_result = _enrich_publication_time_from_html(
                        hit.result,
                        content=document.content,
                        reference=reference_time,
                    )
                else:
                    content = ""
                    enriched_result = hit.result
                enriched_result = _with_inferred_publication_time(
                    enriched_result,
                    reference=reference_time,
                )
                enriched.append(
                    _ResearchHit(
                        instruction=hit.instruction,
                        result=enriched_result,
                        content_excerpt=_compact_excerpt(content),
                        page_read_error=None,
                    )
                )
            except Exception as error:
                enriched.append(
                    _ResearchHit(
                        instruction=hit.instruction,
                        result=hit.result,
                        content_excerpt="",
                        page_read_error=f"{type(error).__name__}: {error}"[:500],
                    )
                )
    finally:
        if reader is not None:
            reader.close()
    return tuple(enriched), page_reads


def _research_discovery_items(
    hits: tuple[_ResearchHit, ...],
    *,
    request: HuntingRequestRecord,
    now: datetime,
    plan: EventResearchPlan,
    source_ids: dict[str, str],
    recalled_companies: tuple[LocalCompanyEvidence, ...],
) -> tuple[DiscoveryItem, ...]:
    items: list[DiscoveryItem] = []
    for hit in hits:
        if (
            not _research_hit_is_persistable(hit)
            or not _is_fresh_research_result(
                hit.result,
                as_of_time=request.as_of_time,
            )
        ):
            continue
        hostname = (urlparse(hit.result.url).hostname or "").lower()
        matched_domain = next(
            (
                domain
                for domain in source_ids
                if hostname == domain or hostname.endswith(f".{domain}")
            ),
            None,
        )
        if matched_domain is None or not hit.result.title.strip():
            continue
        company = _mentioned_company(hit, recalled_companies)
        external_id = hashlib.sha256(hit.result.url.encode()).hexdigest()
        published_at = hit.result.published_at or request.as_of_time
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            published_at = published_at.replace(
                tzinfo=request.as_of_time.tzinfo
            )
        items.append(
            DiscoveryItem(
                discovery_id=(
                    f"{source_ids[matched_domain]}:{external_id[:32]}"
                ),
                source_id=source_ids[matched_domain],
                external_id=external_id,
                security_code=company.code if company is not None else None,
                security_name=company.name if company is not None else None,
                headline=hit.result.title.strip()[:300],
                published_at=published_at,
                collected_at=now,
                source_url=hit.result.url,
                attachment_url=None,
                payload={
                    "snippet": hit.result.snippet,
                    "content": hit.content_excerpt,
                    "search_query": hit.instruction.query,
                    "search_purpose": hit.instruction.purpose,
                    "search_intermediary": "free_public_search",
                    "published_at_known": hit.result.published_at is not None,
                    "publication_time_known": (
                        hit.result.publication_time_known
                        if hit.result.publication_time_known is not None
                        else hit.result.published_at is not None
                    ),
                    "page_read_succeeded": bool(hit.content_excerpt),
                    "page_read_error": hit.page_read_error,
                    "research_parent_request_id": request.request_id,
                    "research_event_hypothesis": plan.event_hypothesis,
                    "research_affected_product": (
                        plan.affected_product_or_service
                    ),
                    "research_chain_nodes": [
                        node.model_dump() for node in plan.chain_nodes
                    ],
                    "research_company_recall_terms": (
                        plan.company_recall_terms
                    ),
                },
            )
        )
    return tuple(items)


def _candidate_discovery_instructions(
    plan: EventResearchPlan,
    *,
    sources: tuple[RegisteredSource, ...],
    operation_budget: int,
) -> list[SearchInstruction]:
    if operation_budget <= 0:
        return []
    domain_priority = {
        "eastmoney.com": 0,
        "10jqka.com.cn": 1,
        "cls.cn": 2,
    }
    domains = tuple(
        dict.fromkeys(
            source.domain
            for source in sorted(
                sources,
                key=lambda item: (
                    domain_priority.get(item.domain, 100),
                    item.source_tier,
                    item.source_id,
                ),
            )
            if source.source_tier <= 3
            and source.source_type == "financial_media"
            and source.domain in domain_priority
        )
    )
    if not domains:
        return []
    terms = [
        plan.affected_product_or_service,
        *plan.company_recall_terms,
        *(node.name for node in plan.chain_nodes),
    ]
    instructions: list[SearchInstruction] = []
    for index, term in enumerate(dict.fromkeys(terms)):
        if len(instructions) >= operation_budget:
            break
        compact_term = " ".join(term.split())[:42]
        instructions.append(
            SearchInstruction(
                query=(
                    f"{compact_term} A股 上市公司 主营业务 "
                    "生产企业 供应商"
                )[:100],
                target_domains=[domains[index % len(domains)]],
                purpose=(
                    f"扩展召回主营生产或供应{compact_term}的合格公司"
                )[:200],
            )
        )
    return instructions


def _relationship_discovery_instructions(
    plan: EventResearchPlan,
    *,
    universe: EligibleUniverseSnapshot,
    sources: tuple[RegisteredSource, ...],
    operation_budget: int,
) -> list[SearchInstruction]:
    if operation_budget <= 0:
        return []
    eligible_names = {item.name for item in universe.securities}
    unresolved_entities = [
        name
        for name in plan.candidate_company_names
        if name not in eligible_names
        and not INELIGIBLE_MODEL_NAME.match(name)
        and len(name) >= 4
    ]
    if not unresolved_entities:
        return []
    preferred_domains = [
        domain
        for domain in ("cninfo.com.cn", "eastmoney.com", "sse.com.cn", "szse.cn")
        if any(source.domain == domain for source in sources)
    ]
    if not preferred_domains:
        return []
    return [
        SearchInstruction(
            query=(
                f"{entity} A股 上市公司 母公司 控股 持股 并表"
            )[:100],
            target_domains=preferred_domains[:2],
            purpose=(
                f"核实{entity}与A股上市公司的控股、持股或并表关系"
            )[:200],
        )
        for entity in dict.fromkeys(unresolved_entities)
    ][:operation_budget]


def _company_search_instructions(
    companies: tuple[LocalCompanyEvidence, ...],
    *,
    product: str,
    sources: tuple[RegisteredSource, ...],
    as_of_time: datetime,
    operation_budget: int,
) -> list[SearchInstruction]:
    if operation_budget <= 0 or not companies:
        return []
    domain_priority = {
        "cninfo.com.cn": 0,
        "eastmoney.com": 1,
        "10jqka.com.cn": 2,
        "cls.cn": 3,
        "szse.cn": 4,
        "sse.com.cn": 5,
    }
    preferred_domains = tuple(
        dict.fromkeys(
            source.domain
            for source in sorted(
                sources,
                key=lambda item: (
                    domain_priority.get(item.domain, 100),
                    item.source_tier,
                    item.source_id,
                ),
            )
            if source.source_tier <= 3
            and source.source_type
            in {"exchange_disclosure", "financial_media"}
        )
    )
    if not preferred_domains:
        return []
    instructions: list[SearchInstruction] = []
    official_domain = next(
        (
            domain
            for domain in preferred_domains
            if domain in {"cninfo.com.cn", "sse.com.cn", "szse.cn"}
        ),
        preferred_domains[0],
    )
    realization_domain = next(
        (
            domain
            for domain in preferred_domains
            if domain in {"eastmoney.com", "10jqka.com.cn", "cls.cn"}
        ),
        official_domain,
    )
    compact_product = " ".join(product.split())[:36]
    current_period = f"{as_of_time.year}年{as_of_time.month}月"
    for company in companies:
        if len(instructions) >= operation_budget:
            break
        company_product = _company_search_product(
            company,
            event_product=compact_product,
        )
        instructions.append(
            SearchInstruction(
                query=(
                    f"{company.name} {company_product} 主营构成 "
                    "订单 客户 销量 售价 收入 利润 "
                    f"{current_period}"
                )[:100],
                target_domains=list(
                    dict.fromkeys((realization_domain, official_domain))
                ),
                purpose=(
                    f"核实{company.name}主营相关性及需求、销售、利润"
                    "兑现证据"
                ),
            )
        )
    return instructions


def _company_search_product(
    company: LocalCompanyEvidence,
    *,
    event_product: str,
) -> str:
    compact_name = re.sub(
        r"(股份有限公司|有限责任公司|股份|集团|公司)$",
        "",
        company.name,
    )
    brand_tokens = [
        compact_name[index:]
        for index in range(max(0, len(compact_name) - 4), len(compact_name) - 1)
        if len(compact_name[index:]) >= 2
    ]
    if any(token in event_product for token in brand_tokens):
        return event_product
    if company.main_business_name:
        return " ".join(company.main_business_name.split())[:36]
    return ""


def _save_source_event_search_run(
    path: Path,
    *,
    request: HuntingRequestRecord,
    planned: EventResearchPlanResult,
    gap_instructions: list[SearchInstruction],
    candidate_instructions: list[SearchInstruction],
    company_instructions: list[SearchInstruction],
    recalled_companies: tuple[LocalCompanyEvidence, ...],
    hits: tuple[_ResearchHit, ...],
    search_operations: int,
    page_reads: int,
    original_page_reads: int,
    started_at: datetime,
    completed_at: datetime,
) -> None:
    plan_json = json.dumps(
        {
            "event_research": planned.plan.model_dump(),
            "gap_searches": [
                instruction.model_dump()
                for instruction in gap_instructions
            ],
            "candidate_discovery_searches": [
                instruction.model_dump()
                for instruction in candidate_instructions
            ],
            "company_searches": [
                instruction.model_dump()
                for instruction in company_instructions
            ],
            "recalled_companies": [
                {
                    "code": company.code,
                    "name": company.name,
                    "availability": company.availability,
                    "main_business_name": company.main_business_name,
                }
                for company in recalled_companies
            ],
            "budgets": {
                "original_source_page_reads": (
                    MAX_ORIGINAL_SOURCE_PAGE_READS
                ),
                "event_search_operations": MAX_EVENT_SEARCH_OPERATIONS,
                "event_page_reads": MAX_EVENT_PAGE_READS,
                "candidate_discovery_search_operations": (
                    MAX_CANDIDATE_DISCOVERY_SEARCH_OPERATIONS
                ),
                "candidate_discovery_page_reads": (
                    MAX_CANDIDATE_DISCOVERY_PAGE_READS
                ),
                "company_closure_search_operations": (
                    MAX_COMPANY_CLOSURE_SEARCH_OPERATIONS
                ),
                "company_closure_page_reads": (
                    MAX_COMPANY_CLOSURE_PAGE_READS
                ),
                "actual_search_operations": search_operations,
                "actual_page_reads": page_reads,
                "actual_original_page_reads": original_page_reads,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    results_json = json.dumps(
        [
            {
                "title": hit.result.title,
                "url": hit.result.url,
                "snippet": hit.result.snippet,
                "published_at": (
                    hit.result.published_at.isoformat()
                    if hit.result.published_at is not None
                    else None
                ),
                "publication_time_known": (
                    hit.result.publication_time_known
                ),
                "content_excerpt": hit.content_excerpt,
                "page_read_error": hit.page_read_error,
                "query": hit.instruction.query,
                "purpose": hit.instruction.purpose,
            }
            for hit in hits
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _insert_search_run(
        path,
        request=request,
        plan_json=plan_json,
        results_json=results_json,
        model=planned.model_result.model,
        prompt_version=EVENT_RESEARCH_PROMPT_VERSION,
        started_at=started_at,
        completed_at=completed_at,
    )


def _research_recall_terms(plan: EventResearchPlan) -> list[str]:
    return list(
        dict.fromkeys(
            [
                plan.affected_product_or_service,
                *plan.company_recall_terms,
                *(node.name for node in plan.chain_nodes),
                *(
                    term
                    for node in plan.chain_nodes
                    for term in node.search_terms
                ),
            ]
        )
    )


def _normalize_event_research_plan(
    plan: EventResearchPlan,
    *,
    as_of_time: datetime,
    allowed_domains: frozenset[str],
) -> EventResearchPlan:
    searches: list[SearchInstruction] = []
    for instruction in plan.searches:
        domains = list(
            dict.fromkeys(
                domain
                for domain in instruction.target_domains
                if domain in allowed_domains
            )
        )
        if not domains:
            continue
        searches.append(
            instruction.model_copy(
                update={
                    "query": _normalize_query_years(
                        instruction.query,
                        as_of_time.year,
                    ),
                    "target_domains": domains,
                }
            )
        )
    if not searches:
        fallback = _fallback_event_research_plan(
            (),
            as_of_time=as_of_time,
            allowed_domains=allowed_domains,
            headline=plan.event_hypothesis,
        )
        searches = fallback.searches
    candidate_names = list(
        dict.fromkeys(
            name.strip()
            for name in plan.candidate_company_names
            if name.strip()
            and not INELIGIBLE_MODEL_NAME.match(name.strip())
        )
    )[:MAX_RECALLED_COMPANIES]
    return plan.model_copy(
        update={
            "searches": searches,
            "candidate_company_names": candidate_names,
        }
    )


def _fallback_event_research_plan(
    items: tuple[DiscoveryItem, ...],
    *,
    as_of_time: datetime,
    allowed_domains: frozenset[str],
    headline: str | None = None,
) -> EventResearchPlan:
    seed = (
        headline
        or " ".join(item.headline for item in items)
        or "产业供需事件"
    )
    compact_seed = " ".join(seed.split())[:60]
    domains = sorted(allowed_domains)[:2]
    return EventResearchPlan(
        event_hypothesis=compact_seed,
        affected_product_or_service=compact_seed[:120],
        chain_nodes=[
            ChainNode(
                name="待核实供需节点",
                role="core",
                evidence_need="供给、需求、库存、订单、产能和交付证据",
                search_terms=[compact_seed],
            )
        ],
        company_recall_terms=[compact_seed],
        candidate_company_names=[],
        searches=[
            SearchInstruction(
                query=(
                    f"{compact_seed} 供应 需求 订单 产能 "
                    f"{as_of_time.year}"
                )[:100],
                target_domains=domains,
                purpose="核实事件是否形成真实供需变化",
            )
        ],
    )


def _fallback_gap_search_plan(
    plan: EventResearchPlan,
    *,
    as_of_time: datetime,
    allowed_domains: frozenset[str],
) -> list[SearchInstruction]:
    current_fact_domains = [
        domain
        for domain in (
            "cls.cn",
            "eastmoney.com",
            "10jqka.com.cn",
            "cninfo.com.cn",
            "sse.com.cn",
            "szse.cn",
        )
        if domain in allowed_domains
    ]
    official_aggregate_domains = [
        domain
        for domain in (
            "stats.gov.cn",
            "customs.gov.cn",
            "miit.gov.cn",
            "ndrc.gov.cn",
            "mofcom.gov.cn",
        )
        if domain in allowed_domains
    ]
    if not current_fact_domains:
        current_fact_domains = (
            official_aggregate_domains or sorted(allowed_domains)
        )
    demand_domains = current_fact_domains[:2]
    supply_domains = [
        *current_fact_domains[:1],
        *official_aggregate_domains[:1],
    ] or demand_domains
    product = " ".join(plan.affected_product_or_service.split())[:45]
    current_period = f"{as_of_time.year}年{as_of_time.month}月"
    return [
        SearchInstruction(
            query=(
                f"{product} 当前需求 订单 出货量 消费量 库存 "
                f"{current_period}"
            )[:100],
            target_domains=demand_domains,
            purpose="补齐当前新增需求和下游采购证据",
        ),
        SearchInstruction(
            query=(
                f"{product} 供应 产能利用率 开工率 价格 交付周期 "
                f"{current_period}"
            )[:100],
            target_domains=supply_domains,
            purpose="补齐供需缺口、价格和交付变化证据",
        ),
    ]


def _normalize_query_years(query: str, current_year: int) -> str:
    return YEAR_PATTERN.sub(
        lambda match: (
            match.group(0)
            if int(match.group(0)) == current_year
            else str(current_year)
        ),
        query,
    )


def _duckduckgo_target_url(href: str) -> str:
    normalized = unescape(href)
    if normalized.startswith("//"):
        normalized = f"https:{normalized}"
    parsed = urlparse(normalized)
    if parsed.hostname not in {"duckduckgo.com", "html.duckduckgo.com"}:
        return normalized
    values = parse_qs(parsed.query).get("uddg")
    return values[0] if values else ""


def _search_result_date(
    text: str,
    *,
    reference: datetime,
) -> datetime | None:
    match = ISO_DATE_PATTERN.search(text) or CHINESE_DATE_PATTERN.search(text)
    if match is None:
        return None
    try:
        value = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=reference.tzinfo,
        )
    except ValueError:
        return None
    if value > reference:
        return None
    return value


def _enrich_publication_time_from_html(
    result: PublicSearchResult,
    *,
    content: bytes,
    reference: datetime,
) -> PublicSearchResult:
    if result.publication_time_known is True:
        return result
    parser = _PublicationTimeParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    for raw_value in parser.values:
        try:
            published_at = datetime.fromisoformat(
                raw_value.strip().replace("Z", "+00:00")
            )
        except ValueError:
            match = ISO_DATETIME_PATTERN.search(
                raw_value
            ) or CHINESE_DATETIME_PATTERN.search(raw_value)
            if match is None:
                continue
            try:
                published_at = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                    int(match.group(6) or 0),
                    tzinfo=reference.tzinfo,
                )
            except ValueError:
                continue
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            published_at = published_at.replace(tzinfo=reference.tzinfo)
        if published_at > reference:
            continue
        return PublicSearchResult(
            title=result.title,
            url=result.url,
            snippet=result.snippet,
            published_at=published_at,
            publication_time_known=True,
        )
    return result


def _url_date(
    url: str,
    *,
    reference: datetime,
) -> datetime | None:
    match = URL_DATE_PATTERN.search(url)
    if match is None:
        return None
    try:
        value = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except ValueError:
        return None
    value = value.replace(tzinfo=reference.tzinfo)
    return value if value <= reference else None


def _with_inferred_publication_time(
    result: PublicSearchResult,
    *,
    reference: datetime,
) -> PublicSearchResult:
    if result.publication_time_known is True and result.published_at is not None:
        return result
    if (
        result.publication_time_known is None
        and result.published_at is not None
    ):
        return PublicSearchResult(
            title=result.title,
            url=result.url,
            snippet=result.snippet,
            published_at=result.published_at,
            publication_time_known=True,
        )
    # 搜索摘要中的日期可能只是合同、会议或报告期，而不是网页发布时间。
    # 只有网址自身携带的日期可以在读取正文前提升为“发布时间已知”；
    # 页面元数据则由 _enrich_publication_time_from_html 单独核实。
    published_at = _url_date(result.url, reference=reference)
    if published_at is None:
        return result
    return PublicSearchResult(
        title=result.title,
        url=result.url,
        snippet=result.snippet,
        published_at=published_at,
        publication_time_known=True,
    )


def _result_matches_instruction(
    result: PublicSearchResult,
    *,
    instruction: SearchInstruction,
) -> bool:
    if _is_low_value_search_result(result):
        return False
    match = TARGETED_RESEARCH_PURPOSE.match(instruction.purpose)
    if match is None:
        return True
    target = match.group(1).strip()
    corpus = " ".join((result.title, result.snippet))
    return company_name_mentioned(target, corpus)


def _is_low_value_search_result(result: PublicSearchResult) -> bool:
    hostname = (urlparse(result.url).hostname or "").lower()
    return (
        hostname in LOW_VALUE_RESULT_HOSTS
        or LOW_VALUE_RESULT_TITLE.search(result.title) is not None
    )


def _research_hit_is_persistable(hit: _ResearchHit) -> bool:
    return bool(
        not _is_low_value_search_result(hit.result)
        and hit.content_excerpt.strip()
        and hit.result.published_at is not None
        and hit.result.publication_time_known is True
    )


def _is_fresh_research_result(
    result: PublicSearchResult,
    *,
    as_of_time: datetime,
) -> bool:
    published_at = result.published_at or _url_date(
        result.url,
        reference=as_of_time,
    )
    if published_at is None:
        return True
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        published_at = published_at.replace(tzinfo=as_of_time.tzinfo)
    return as_of_time - MAX_RESEARCH_RESULT_AGE <= published_at <= as_of_time


def _source_event_discovery_ids(
    request: HuntingRequestRecord,
) -> tuple[str, ...]:
    payload = json.loads(request.trigger_content)
    values = payload.get("discovery_ids")
    if not isinstance(values, list) or not values:
        raise ValueError("来源事件缺少 discovery_ids")
    return tuple(str(value) for value in values)


def _discovery_excerpt(item: DiscoveryItem) -> str:
    for key in ("content", "brief", "snippet", "headline"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return item.headline


def _compact_excerpt(value: str) -> str:
    return " ".join(value.split())[:MAX_RESEARCH_EXCERPT_CHARS]


def _mentioned_company(
    hit: _ResearchHit,
    companies: tuple[LocalCompanyEvidence, ...],
) -> LocalCompanyEvidence | None:
    corpus = " ".join(
        (
            hit.result.title,
            hit.result.snippet,
            hit.content_excerpt,
        )
    )
    purpose_match = TARGETED_RESEARCH_PURPOSE.match(
        hit.instruction.purpose
    )
    if purpose_match is not None:
        targeted_name = purpose_match.group(1).strip()
        targeted_company = next(
            (
                company
                for company in companies
                if company.name == targeted_name
            ),
            None,
        )
        if targeted_company is not None:
            return (
                targeted_company
                if company_name_mentioned(targeted_company.name, corpus)
                else None
            )
    return next(
        (
            company
            for company in sorted(
                companies,
                key=lambda item: len(item.name),
                reverse=True,
            )
            if company.name and company_name_mentioned(company.name, corpus)
        ),
        None,
    )


def _deduplicate_hits(
    hits: tuple[_ResearchHit, ...],
) -> tuple[_ResearchHit, ...]:
    by_url: dict[str, _ResearchHit] = {}
    for hit in hits:
        existing = by_url.get(hit.result.url)
        if existing is None or len(hit.content_excerpt) > len(
            existing.content_excerpt
        ):
            by_url[hit.result.url] = hit
    return tuple(by_url.values())


def _save_search_run(
    path: Path,
    *,
    request: HuntingRequestRecord,
    planned: SearchPlanResult,
    results: tuple[PublicSearchResult, ...],
    started_at: datetime,
    completed_at: datetime,
) -> None:
    results_json = json.dumps(
        [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "published_at": (
                    item.published_at.isoformat()
                    if item.published_at is not None
                    else None
                ),
            }
            for item in results
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _insert_search_run(
        path,
        request=request,
        plan_json=planned.plan.model_dump_json(),
        results_json=results_json,
        model=planned.model_result.model,
        prompt_version=SEARCH_PROMPT_VERSION,
        started_at=started_at,
        completed_at=completed_at,
    )


def _insert_search_run(
    path: Path,
    *,
    request: HuntingRequestRecord,
    plan_json: str,
    results_json: str,
    model: str,
    prompt_version: str,
    started_at: datetime,
    completed_at: datetime,
) -> None:
    input_sha256 = hashlib.sha256(
        f"{prompt_version}|{request.trigger_content}".encode()
    ).hexdigest()
    timestamp = completed_at.isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_search_runs (
                search_run_id, request_id, input_text, plan_json, results_json,
                model, prompt_version, input_sha256, started_at, completed_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"search-{uuid.uuid4().hex}",
                request.request_id,
                request.trigger_content,
                plan_json,
                results_json,
                model,
                prompt_version,
                input_sha256,
                started_at.isoformat(),
                timestamp,
                timestamp,
            ),
        )
