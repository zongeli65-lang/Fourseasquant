from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fourseasquant.sqlite_connection import open_database_connection

from .discovery import DiscoveryItem
from .discovery import read_discovery_items


NEWS_FRESHNESS = timedelta(hours=72)
MAX_COLLECTED_PER_SOURCE_POLL = 8
MAX_FRESH_TRIAGE_BACKLOG = 24
MAX_DEEP_HUNTS_PER_TRIAGE = 3
MINIMUM_NEWS_VALUE_SCORE = 5

_AGGREGATE_OR_MARKET_ONLY = re.compile(
    r"(涨停分析|板块震荡|震荡反弹|概念异动|大盘|收盘综述|"
    r"今日投资舆情热点|早间新闻精选|晚间新闻汇总|"
    r"玩转ETF|ETF|股票交易异常波动|股票异常波动|"
    r"股价异常波动)"
)
_POLICY_ONLY = re.compile(
    r"(印发|发布.*规划|指导意见|征求意见|政策解读|外交部|"
    r"工信部：|商务部发布)"
)
_MAJOR_ORDER = re.compile(
    r"(重大订单|签订.{0,16}(合同|订单)|中标|批量供货|"
    r"批量交付|获得.{0,12}订单|订单排产)"
)
_DEMAND = re.compile(
    r"(需求增长|需求爆发|客户采购|客户下单|订单增长|"
    r"销量增长|销售增长|供不应求|满产满销)"
)
_SUPPLY_CONTRACTION = re.compile(
    r"(停产|暂停生产|减产|限产|供应中断|供应短缺|缺货|"
    r"库存下降|库存去化|交付周期延长)"
)
_PRICE = re.compile(r"(涨价|提价|价格上涨|售价上涨|报价上调)")
_REALIZATION = re.compile(
    r"(产能利用率|开工率|销量|销售量|营业收入|毛利润|净利润|"
    r"发电量|上网电量).{0,20}(增长|增加|提升|同比|环比|达到|实现)"
)
_CAPACITY_ONLY = re.compile(r"(扩产|投产|爬产|新建产线|中试线|新增产能)")
_HISTORICAL_ONLY = re.compile(r"(上半年|去年|上年度|历史统计|二季度|一季度)")


@dataclass(frozen=True)
class FreshNewsFunnel:
    collected_count: int
    triaged_count: int
    pending_triage_count: int
    research_material_count: int
    freshness_cutoff: datetime


def select_news_for_collection(
    items: tuple[DiscoveryItem, ...],
    *,
    as_of_time: datetime,
    limit: int = MAX_COLLECTED_PER_SOURCE_POLL,
) -> tuple[DiscoveryItem, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("精选新闻上限必须在1到100之间")
    cutoff = as_of_time - NEWS_FRESHNESS
    scored = [
        (news_value_score(item), item)
        for item in items
        if cutoff <= item.published_at <= as_of_time
    ]
    selected = [
        item
        for score, item in sorted(
            scored,
            key=lambda row: (
                -row[0],
                -row[1].published_at.timestamp(),
                row[1].discovery_id,
            ),
        )
        if score >= MINIMUM_NEWS_VALUE_SCORE
    ]
    return tuple(selected[:limit])


def news_value_score(item: DiscoveryItem) -> int:
    headline = item.headline.strip()
    text = " ".join((headline, _content_excerpt(item)))
    if _AGGREGATE_OR_MARKET_ONLY.search(headline):
        return -100
    score = 0
    if item.source_id == "cninfo":
        score += 3
    if item.security_code is not None:
        score += 3
    if _MAJOR_ORDER.search(text):
        score += 10
    if _DEMAND.search(text):
        score += 8
    if _SUPPLY_CONTRACTION.search(text):
        score += 8
    if _PRICE.search(text):
        score += 6
    if _REALIZATION.search(text):
        score += 7
    if _CAPACITY_ONLY.search(text):
        score += 3
    if _POLICY_ONLY.search(headline):
        score -= 8
    if _HISTORICAL_ONLY.search(text):
        score -= 4
    return score


def read_fresh_news_funnel(
    path: Path,
    *,
    as_of_time: datetime,
) -> FreshNewsFunnel:
    cutoff = as_of_time - NEWS_FRESHNESS
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            WITH fresh_primary AS (
                SELECT discovery.discovery_id
                FROM industry_chain_discovery_items AS discovery
                WHERE discovery.published_at >= ?
                  AND discovery.published_at <= ?
                  AND json_extract(
                        discovery.payload_json,
                        '$.research_parent_request_id'
                      ) IS NULL
            ),
            triaged AS (
                SELECT DISTINCT triaged.value AS discovery_id
                FROM industry_chain_triage_runs AS triage,
                     json_each(triage.discovery_ids_json) AS triaged
            )
            SELECT
                (SELECT COUNT(*) FROM fresh_primary),
                (
                    SELECT COUNT(*)
                    FROM fresh_primary
                    JOIN triaged USING (discovery_id)
                ),
                (
                    SELECT COUNT(*)
                    FROM industry_chain_discovery_items AS discovery
                    WHERE discovery.published_at >= ?
                      AND discovery.published_at <= ?
                      AND json_extract(
                            discovery.payload_json,
                            '$.research_parent_request_id'
                          ) IS NOT NULL
                )
            """,
            (
                cutoff.isoformat(),
                as_of_time.isoformat(),
                cutoff.isoformat(),
                as_of_time.isoformat(),
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("新鲜新闻漏斗统计失败")
    collected_count = int(row[0])
    triaged_count = int(row[1])
    return FreshNewsFunnel(
        collected_count=collected_count,
        triaged_count=triaged_count,
        pending_triage_count=max(0, collected_count - triaged_count),
        research_material_count=int(row[2]),
        freshness_cutoff=cutoff,
    )


def prune_untriaged_news(
    path: Path,
    *,
    as_of_time: datetime,
    keep: int = MAX_FRESH_TRIAGE_BACKLOG,
) -> int:
    if keep < 1:
        raise ValueError("待初筛新闻保留量必须大于零")
    cutoff = as_of_time - NEWS_FRESHNESS
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT discovery.discovery_id
            FROM industry_chain_discovery_items AS discovery
            WHERE discovery.published_at >= ?
              AND discovery.published_at <= ?
              AND json_extract(
                    discovery.payload_json,
                    '$.research_parent_request_id'
                  ) IS NULL
              AND NOT EXISTS (
                    SELECT 1
                    FROM industry_chain_triage_runs AS triage,
                         json_each(triage.discovery_ids_json) AS triaged
                    WHERE triaged.value = discovery.discovery_id
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM industry_chain_hunting_requests AS request,
                         json_each(
                           CASE
                             WHEN json_valid(request.trigger_content)
                             THEN request.trigger_content
                             ELSE '{}'
                           END,
                           '$.discovery_ids'
                         ) AS requested
                    WHERE request.status IN (
                            'queued', 'running', 'paused'
                          )
                      AND requested.value = discovery.discovery_id
              )
            """,
            (cutoff.isoformat(), as_of_time.isoformat()),
        ).fetchall()
    discovery_ids = tuple(str(row[0]) for row in rows)
    if len(discovery_ids) <= keep:
        return 0
    items = read_discovery_items(path, discovery_ids)
    ordered = sorted(
        items,
        key=lambda item: (
            -news_value_score(item),
            -item.published_at.timestamp(),
            item.discovery_id,
        ),
    )
    delete_ids = tuple(item.discovery_id for item in ordered[keep:])
    placeholders = ",".join("?" for _ in delete_ids)
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            f"""
            DELETE FROM industry_chain_discovery_items
            WHERE discovery_id IN ({placeholders})
            """,
            delete_ids,
        )
    return cursor.rowcount


def cancel_low_value_queued_event_hunts(
    path: Path,
    *,
    as_of_time: datetime,
) -> int:
    """取消不再符合当前供需精选标准的排队任务，但保留完整审计。"""
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT request.request_id, requested.value
            FROM industry_chain_hunting_requests AS request,
                 json_each(
                     CASE
                         WHEN json_valid(request.trigger_content)
                         THEN request.trigger_content
                         ELSE '{}'
                     END,
                     '$.discovery_ids'
                 ) AS requested
            WHERE request.status = 'queued'
              AND request.trigger_method = 'new_evidence'
              AND request.trigger_type = 'source_event'
            ORDER BY request.requested_at, request.request_id
            """
        ).fetchall()
    discovery_ids = tuple(dict.fromkeys(str(row[1]) for row in rows))
    items = read_discovery_items(path, discovery_ids)
    score_by_id = {
        item.discovery_id: news_value_score(item) for item in items
    }
    scores_by_request: dict[str, list[int]] = {}
    for request_id, discovery_id in rows:
        scores_by_request.setdefault(str(request_id), []).append(
            score_by_id.get(str(discovery_id), -100)
        )
    cancelled_ids = tuple(
        request_id
        for request_id, scores in scores_by_request.items()
        if not scores or max(scores) < MINIMUM_NEWS_VALUE_SCORE
    )
    if not cancelled_ids:
        return 0
    placeholders = ",".join("?" for _ in cancelled_ids)
    with open_database_connection(path) as connection:
        cursor = connection.execute(
            f"""
            UPDATE industry_chain_hunting_requests
            SET status = 'cancelled', completed_at = ?,
                error_summary = '不符合当前供需精选标准，停止深挖'
            WHERE status = 'queued'
              AND request_id IN ({placeholders})
            """,
            (as_of_time.isoformat(), *cancelled_ids),
        )
    return cursor.rowcount


def _content_excerpt(item: DiscoveryItem) -> str:
    for key in ("content", "brief", "snippet", "text"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:2000]
    return ""
