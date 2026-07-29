from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast


SOURCE_CONFIG_VERSION = "industry-chain-sources-v0.4"
PREVIOUS_SOURCE_CONFIG_VERSION = "industry-chain-sources-v0.3"
LEGACY_SOURCE_CONFIG_VERSION = "industry-chain-sources-v0.2"
LifecycleState = Literal["active", "observing", "disabled"]


@dataclass(frozen=True)
class SeedSource:
    source_id: str
    source_name: str
    base_url: str
    domain: str
    source_tier: int
    source_type: str
    categories: tuple[str, ...]
    lifecycle_state: LifecycleState
    poll_interval_minutes: int
    allow_browser: bool
    source_version: int = 1
    config_version: str = SOURCE_CONFIG_VERSION


@dataclass(frozen=True)
class RegisteredSource:
    source_id: str
    source_version: int
    source_name: str
    base_url: str
    domain: str
    source_tier: int
    source_type: str
    categories: tuple[str, ...]
    access_class: str
    lifecycle_state: LifecycleState
    poll_interval_minutes: int
    allow_browser: bool
    config_version: str
    effective_at: datetime


LEGACY_SEED_SOURCES: tuple[SeedSource, ...] = (
    SeedSource(
        "cninfo",
        "巨潮资讯网",
        "https://www.cninfo.com.cn/",
        "cninfo.com.cn",
        1,
        "exchange_disclosure",
        ("company_disclosure", "announcement"),
        "active",
        3,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "sse",
        "上海证券交易所",
        "https://www.sse.com.cn/",
        "sse.com.cn",
        1,
        "exchange_disclosure",
        ("company_disclosure", "announcement"),
        "active",
        3,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "szse",
        "深圳证券交易所",
        "https://www.szse.cn/",
        "szse.cn",
        1,
        "exchange_disclosure",
        ("company_disclosure", "announcement"),
        "active",
        3,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "stats-cn",
        "国家统计局",
        "https://data.stats.gov.cn/",
        "stats.gov.cn",
        1,
        "government_statistics",
        ("supply_demand", "price", "production"),
        "active",
        15,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "customs-cn",
        "海关总署",
        "https://online.customs.gov.cn/",
        "customs.gov.cn",
        1,
        "government_statistics",
        ("supply_demand", "import_export"),
        "active",
        15,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "miit-cn",
        "工业和信息化部",
        "https://www.miit.gov.cn/",
        "miit.gov.cn",
        1,
        "government_statistics",
        ("supply_demand", "industry_operation"),
        "active",
        15,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "ndrc-cn",
        "国家发展改革委",
        "https://www.ndrc.gov.cn/",
        "ndrc.gov.cn",
        1,
        "government",
        ("supply_demand", "price", "policy_watch"),
        "active",
        15,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "mofcom-cn",
        "商务部商务预报",
        "https://cif.mofcom.gov.cn/",
        "mofcom.gov.cn",
        1,
        "government_statistics",
        ("supply_demand", "price", "inventory"),
        "active",
        15,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "eastmoney-discovery",
        "东方财富新闻发现",
        "https://finance.eastmoney.com/",
        "eastmoney.com",
        4,
        "news_discovery",
        ("news_discovery",),
        "observing",
        3,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "ths-discovery",
        "同花顺新闻发现",
        "https://news.10jqka.com.cn/",
        "10jqka.com.cn",
        4,
        "news_discovery",
        ("news_discovery",),
        "observing",
        3,
        True,
        config_version=LEGACY_SOURCE_CONFIG_VERSION,
    ),
)

PREVIOUS_SEED_SOURCES: tuple[SeedSource, ...] = (
    *(
        SeedSource(
            source.source_id,
            source.source_name,
            source.base_url,
            source.domain,
            source.source_tier,
            source.source_type,
            source.categories,
            source.lifecycle_state,
            source.poll_interval_minutes,
            source.allow_browser,
            source_version=2,
            config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
        )
        for source in LEGACY_SEED_SOURCES[:8]
    ),
    SeedSource(
        "eastmoney-discovery",
        "东方财富财经新闻",
        "https://finance.eastmoney.com/",
        "eastmoney.com",
        3,
        "financial_media",
        ("news_discovery", "supply_demand", "company_event"),
        "active",
        3,
        True,
        source_version=2,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "ths-discovery",
        "同花顺财经新闻",
        "https://news.10jqka.com.cn/",
        "10jqka.com.cn",
        3,
        "financial_media",
        ("news_discovery", "supply_demand", "company_event"),
        "active",
        3,
        True,
        source_version=2,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "cls-news",
        "财联社",
        "https://www.cls.cn/",
        "cls.cn",
        3,
        "financial_media",
        ("news_discovery", "supply_demand", "company_event"),
        "active",
        3,
        True,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "xueqiu-social",
        "雪球财经社区",
        "https://xueqiu.com/",
        "xueqiu.com",
        4,
        "social_discovery",
        ("news_discovery", "social_signal"),
        "active",
        3,
        True,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "weibo-finance",
        "微博财经内容",
        "https://weibo.com/",
        "weibo.com",
        5,
        "social_discovery",
        ("news_discovery", "social_signal"),
        "active",
        3,
        True,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
    SeedSource(
        "wechat-public",
        "微信公众号公开文章",
        "https://mp.weixin.qq.com/",
        "mp.weixin.qq.com",
        5,
        "social_discovery",
        ("news_discovery", "social_signal"),
        "active",
        3,
        True,
        config_version=PREVIOUS_SOURCE_CONFIG_VERSION,
    ),
)

CURRENT_SEED_SOURCES: tuple[SeedSource, ...] = (
    *(
        SeedSource(
            source.source_id,
            source.source_name,
            source.base_url,
            source.domain,
            source.source_tier,
            source.source_type,
            source.categories,
            source.lifecycle_state,
            (
                10
                if source.source_id
                in {
                    "cninfo",
                    "sse",
                    "szse",
                    "eastmoney-discovery",
                    "ths-discovery",
                    "cls-news",
                }
                else 30
            ),
            source.allow_browser,
            source_version=source.source_version + 1,
        )
        for source in PREVIOUS_SEED_SOURCES
    ),
)


def seed_source_registry(
    connection: sqlite3.Connection,
    *,
    now: datetime,
) -> None:
    timestamp = now.isoformat()
    for source in (
        *LEGACY_SEED_SOURCES,
        *PREVIOUS_SEED_SOURCES,
        *CURRENT_SEED_SOURCES,
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO industry_chain_source_registry (
                source_id, source_version, source_name, base_url, domain,
                source_tier, source_type, categories_json, access_class,
                lifecycle_state, poll_interval_minutes, allow_browser,
                config_version, effective_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'public_no_login', ?, ?, ?, ?, ?, ?)
            """,
            (
                source.source_id,
                source.source_version,
                source.source_name,
                source.base_url,
                source.domain,
                source.source_tier,
                source.source_type,
                json.dumps(source.categories, ensure_ascii=False),
                source.lifecycle_state,
                source.poll_interval_minutes,
                int(source.allow_browser),
                source.config_version,
                timestamp,
                timestamp,
            ),
        )


def read_latest_sources(path: Path) -> tuple[RegisteredSource, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT source_id, source_version, source_name, base_url, domain,
                   source_tier, source_type, categories_json, access_class,
                   lifecycle_state, poll_interval_minutes, allow_browser,
                   config_version, effective_at
            FROM industry_chain_source_registry AS source
            WHERE source_version = (
                SELECT MAX(candidate.source_version)
                FROM industry_chain_source_registry AS candidate
                WHERE candidate.source_id = source.source_id
            )
            ORDER BY source_tier, source_type, source_name
            """
        ).fetchall()
    return tuple(_source_from_row(row) for row in rows)


def registered_domains(path: Path) -> frozenset[str]:
    return frozenset(
        source.domain
        for source in read_latest_sources(path)
        if source.lifecycle_state != "disabled"
    )


def _source_from_row(row: tuple[object, ...]) -> RegisteredSource:
    categories = cast(list[str], json.loads(cast(str, row[7])))
    return RegisteredSource(
        source_id=cast(str, row[0]),
        source_version=cast(int, row[1]),
        source_name=cast(str, row[2]),
        base_url=cast(str, row[3]),
        domain=cast(str, row[4]),
        source_tier=cast(int, row[5]),
        source_type=cast(str, row[6]),
        categories=tuple(categories),
        access_class=cast(str, row[8]),
        lifecycle_state=cast(LifecycleState, row[9]),
        poll_interval_minutes=cast(int, row[10]),
        allow_browser=bool(row[11]),
        config_version=cast(str, row[12]),
        effective_at=datetime.fromisoformat(cast(str, row[13])),
    )
