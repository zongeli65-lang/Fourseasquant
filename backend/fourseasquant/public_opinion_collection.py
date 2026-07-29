from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, cast

import httpx
from pydantic import BaseModel

from fourseasquant.public_opinion_receivers import (
    EASTMONEY_BASE_URL,
    SinaTopicPage,
    parse_eastmoney_detail,
    parse_eastmoney_hot_payload,
    parse_eastmoney_list,
    parse_eastmoney_reply_payload,
    parse_sina_bar_config,
    parse_sina_reply_payload,
    parse_sina_topic_payload,
)
from fourseasquant.public_opinion import (
    ModelOpinionClassification,
    OpinionContent,
    aggregate_public_opinion,
)
from fourseasquant.public_opinion_deepseek import (
    DEEPSEEK_MODEL,
    DeepSeekOpinionClassifier,
    DeepSeekOpinionError,
    PROMPT_VERSION,
)
from fourseasquant.public_opinion_repository import (
    PublicOpinionCollectionJob,
    read_cached_model_classifications,
    read_public_opinion_model_content_ids,
    read_public_opinion_job,
    rebuild_public_opinion_day,
    save_public_opinion_day,
    sync_hot_discovery_snapshot,
    update_public_opinion_job,
)
from fourseasquant.public_opinion_secrets import get_deepseek_api_key


EASTMONEY_HOT_URL = (
    "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
)
SINA_GUBA_BASE_URL = "https://guba.sina.cn/"
SINA_GUBA_API_URL = f"{SINA_GUBA_BASE_URL}api/"


class OpinionBatchClassifier(Protocol):
    def classify(
        self,
        contents: list[OpinionContent],
    ) -> dict[str, ModelOpinionClassification]: ...


class DiscoveryCollectionResult(BaseModel):
    platform: Literal["eastmoney", "tonghuashun"]
    status: Literal["succeeded", "unavailable", "failed"]
    actual_date: date
    stock_count: int
    error_summary: str | None
    collected_at: datetime


class SinaCollectionCursor(BaseModel):
    version: int = 2
    bid: str = ""
    phase: Literal["topics", "replies"] = "topics"
    topic_relate_value: int | None = None
    topic_relate_tid: str | None = None
    page_relate_value: int | None = None
    page_relate_tid: str | None = None
    next_relate_value: int | None = None
    next_relate_tid: str | None = None
    topic_index: int = 0
    reply_cursor: str = ""
    reply_seen_count: int = 0
    finish_after_page: bool = False
    topic_pages_processed: int = 0
    selected_content_count: int = 0
    sample_capped: bool = False
    sample_limit: int | None = None


def collect_eastmoney_hot_discovery(
    path: Path,
    *,
    actual_date: date,
    collected_at: datetime,
    name_by_code: dict[str, str],
    expected_count: int = 100,
    client: httpx.Client,
) -> DiscoveryCollectionResult:
    response = client.post(
        EASTMONEY_HOT_URL,
        json={
            "appId": "appId01",
            "globalId": "786e4c21-70dc-435a-93bb-38",
            "marketType": "",
            "pageNo": 1,
            "pageSize": expected_count,
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
    )
    response.raise_for_status()
    payload = cast(dict[str, object], response.json())
    stocks = parse_eastmoney_hot_payload(
        payload,
        name_by_code=name_by_code,
        expected_count=expected_count,
    )
    sync_hot_discovery_snapshot(
        path,
        platform="eastmoney",
        actual_date=actual_date,
        stocks=stocks,
        collected_at=collected_at,
        complete=True,
    )
    return DiscoveryCollectionResult(
        platform="eastmoney",
        status="succeeded",
        actual_date=actual_date,
        stock_count=len(stocks),
        error_summary=None,
        collected_at=collected_at,
    )


def run_eastmoney_collection_job(
    path: Path,
    *,
    job_id: int,
    client: httpx.Client,
    now: datetime,
    wait: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.1, 0.8),
    max_pages: int = 1_000,
) -> PublicOpinionCollectionJob:
    job = read_public_opinion_job(path, job_id)
    if job.platform != "eastmoney":
        raise ValueError("该执行器只处理东方财富任务")
    if job.status not in {"pending", "failed", "blocked"}:
        raise ValueError("舆论采集任务当前状态不可执行")
    page = _cursor_page(job.cursor)
    update_public_opinion_job(
        path,
        job_id=job.id,
        status="running",
        cursor=f"page:{page}",
        updated_at=now,
    )
    contents_by_date: dict[date, list[OpinionContent]] = {}
    request_count = 0
    listing_complete = False
    reply_error_summary: str | None = None
    error_summary: str | None = None
    try:
        for page_number in range(page, max_pages + 1):
            listing_url = _eastmoney_list_url(job.code, page_number)
            listing_response = _guarded_get(
                client,
                listing_url,
                wait=wait,
                jitter=jitter,
                request_count=request_count,
            )
            request_count += 1
            references = parse_eastmoney_list(
                listing_response.text,
                code=job.code,
            )
            update_public_opinion_job(
                path,
                job_id=job.id,
                status="running",
                cursor=f"page:{page_number}",
                updated_at=datetime.now(now.tzinfo),
            )
            if not references:
                listing_complete = True
                break
            for reference in references:
                detail_response = _guarded_get(
                    client,
                    reference.url,
                    wait=wait,
                    jitter=jitter,
                    request_count=request_count,
                )
                request_count += 1
                topic = parse_eastmoney_detail(
                    detail_response.text,
                    code=job.code,
                    collected_at=now,
                )
                topic_date = topic.published_at.date()
                if job.start_date <= topic_date <= job.end_date:
                    contents_by_date.setdefault(topic_date, []).append(topic)
                if reference.reply_count <= 0:
                    continue
                if reply_error_summary is not None:
                    continue
                reply_page = 1
                collected_reply_count = 0
                try:
                    while collected_reply_count < reference.reply_count:
                        if request_count > 0:
                            wait(5.0 + jitter())
                        reply_response = client.post(
                            (
                                f"{EASTMONEY_BASE_URL}api/getData"
                                f"?code={job.code}"
                                "&path=reply/api/Reply/ArticleNewReplyList"
                            ),
                            data={
                                "param": (
                                    f"postid={reference.content_id}&sort=1"
                                    f"&sorttype=1&p={reply_page}&ps=30"
                                    "&needHide=true"
                                ),
                                "plat": "Web",
                                "path": (
                                    "reply/api/Reply/ArticleNewReplyList"
                                ),
                                "env": "2",
                                "origin": "",
                                "version": "2022",
                                "product": "Guba",
                            },
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        request_count += 1
                        reply_response.raise_for_status()
                        reply_payload = cast(
                            dict[str, object],
                            reply_response.json(),
                        )
                        replies, total = parse_eastmoney_reply_payload(
                            reply_payload,
                            code=job.code,
                            post_id=reference.content_id,
                            collected_at=now,
                        )
                        for reply in replies:
                            reply_date = reply.published_at.date()
                            if job.start_date <= reply_date <= job.end_date:
                                contents_by_date.setdefault(
                                    reply_date,
                                    [],
                                ).append(reply)
                        collected_reply_count += len(replies)
                        if collected_reply_count >= total:
                            break
                        if not replies:
                            raise ValueError("东方财富回复分页提前结束")
                        reply_page += 1
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    reply_error_summary = str(error)
        else:
            error_summary = f"达到最大分页数 {max_pages}，尚未到列表末页"
    except (httpx.HTTPError, ValueError, KeyError) as error:
        error_summary = str(error)

    collection_complete = (
        listing_complete
        and reply_error_summary is None
        and error_summary is None
    )
    if collection_complete:
        current_date = job.start_date
        while current_date <= job.end_date:
            contents_by_date.setdefault(current_date, [])
            current_date += timedelta(days=1)
    for actual_date, contents in contents_by_date.items():
        aggregate = aggregate_public_opinion(
            contents,
            actual_date=actual_date,
            platform="eastmoney",
            code=job.code,
            collection_complete=collection_complete,
            rules_version="public-opinion-v1",
        )
        save_public_opinion_day(
            path,
            contents=contents,
            aggregate=aggregate,
            collected_at=now,
        )
    if collection_complete:
        return update_public_opinion_job(
            path,
            job_id=job.id,
            status="succeeded",
            cursor=f"page:{page_number}",
            updated_at=now,
        )
    return update_public_opinion_job(
        path,
        job_id=job.id,
        status="blocked",
        cursor=f"page:{page_number}",
        updated_at=now,
        error_summary=(
            error_summary
            or reply_error_summary
            or "东方财富采集未完整"
        ),
    )


def run_sina_collection_job(
    path: Path,
    *,
    job_id: int,
    client: httpx.Client,
    classifier: OpinionBatchClassifier,
    now: datetime,
    wait: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.1, 0.8),
    max_pages: int = 1_000,
    max_requests_per_run: int = 1_000,
    max_contents_per_job: int = 300,
    max_replies_per_topic: int = 50,
) -> PublicOpinionCollectionJob:
    job = read_public_opinion_job(path, job_id)
    original_cursor = job.cursor
    if job.platform != "sina":
        raise ValueError("该执行器只处理新浪股吧任务")
    if job.status not in {"pending", "failed", "blocked"}:
        raise ValueError("舆论采集任务当前状态不可执行")
    if max_requests_per_run < 1:
        raise ValueError("单次新浪采集请求预算必须大于零")
    if max_contents_per_job < 1:
        raise ValueError("单股时间窗口采集上限必须大于零")
    if max_replies_per_topic < 1:
        raise ValueError("单主题回复采集上限必须大于零")
    state = _load_sina_cursor(job.cursor)
    state.sample_limit = max_contents_per_job
    known_content_ids = read_public_opinion_model_content_ids(
        path,
        platform="sina",
        code=job.code,
        start_date=job.start_date,
        end_date=job.end_date,
        prompt_version=PROMPT_VERSION,
    )
    state.selected_content_count = max(
        state.selected_content_count,
        len(known_content_ids),
    )
    update_public_opinion_job(
        path,
        job_id=job.id,
        status="running",
        cursor=state.model_dump_json(),
        updated_at=datetime.now(now.tzinfo),
    )
    market = _sina_market(job.code)
    request_count = 0
    contents_by_date: dict[date, list[OpinionContent]] = {}
    listing_complete = False
    global_limit_reached = False
    error_summary: str | None = None
    current_page = None
    try:
        if not state.bid:
            config_response = _guarded_get(
                client,
                f"{SINA_GUBA_BASE_URL}list_{market}{job.code}.html",
                wait=wait,
                jitter=jitter,
                request_count=request_count,
            )
            request_count += 1
            config = parse_sina_bar_config(
                config_response.text,
                expected_code=job.code,
            )
            state.bid = config.bid

        while request_count < max_requests_per_run:
            if state.phase == "topics":
                if state.topic_pages_processed >= max_pages:
                    error_summary = (
                        f"达到最大分页数 {max_pages}，尚未覆盖日期范围"
                    )
                    break
                page_relate_value = state.topic_relate_value
                page_relate_tid = state.topic_relate_tid
                current_page = _fetch_sina_topic_page(
                    client,
                    code=job.code,
                    market=market,
                    bid=state.bid,
                    relate_value=page_relate_value,
                    relate_tid=page_relate_tid,
                    collected_at=now,
                    wait=wait,
                    jitter=jitter,
                    request_count=request_count,
                )
                request_count += 1
                state.topic_pages_processed += 1
                if not current_page.references:
                    listing_complete = True
                    break
                content_by_tid = {
                    content.content_id: content
                    for content in current_page.contents
                }
                for reference in current_page.references:
                    reference_date = reference.published_at.date()
                    if not job.start_date <= reference_date <= job.end_date:
                        continue
                    if reference.tid in known_content_ids:
                        continue
                    if state.selected_content_count >= max_contents_per_job:
                        state.sample_capped = True
                        global_limit_reached = True
                        listing_complete = True
                        break
                    topic = content_by_tid[reference.tid]
                    contents_by_date.setdefault(reference_date, []).append(topic)
                    known_content_ids.add(topic.content_id)
                    state.selected_content_count += 1
                if global_limit_reached:
                    break
                state.phase = "replies"
                state.page_relate_value = page_relate_value
                state.page_relate_tid = page_relate_tid
                state.next_relate_value = current_page.next_relate_value
                state.next_relate_tid = current_page.next_relate_tid
                state.topic_index = 0
                state.reply_cursor = ""
                state.reply_seen_count = 0
                state.finish_after_page = any(
                    reference.published_at.date() < job.start_date
                    for reference in current_page.references
                )

            if state.phase != "replies":
                continue
            if current_page is None:
                if request_count >= max_requests_per_run:
                    break
                current_page = _fetch_sina_topic_page(
                    client,
                    code=job.code,
                    market=market,
                    bid=state.bid,
                    relate_value=state.page_relate_value,
                    relate_tid=state.page_relate_tid,
                    collected_at=now,
                    wait=wait,
                    jitter=jitter,
                    request_count=request_count,
                )
                request_count += 1

            while state.topic_index < len(current_page.references):
                reference = current_page.references[state.topic_index]
                reference_date = reference.published_at.date()
                if (
                    not job.start_date <= reference_date <= job.end_date
                    or reference.reply_count <= 0
                ):
                    _advance_sina_topic(state)
                    continue
                if request_count >= max_requests_per_run:
                    break
                reply_response = _guarded_get_with_params(
                    client,
                    SINA_GUBA_API_URL,
                    params={
                        "s": "h5thread",
                        "a": "getreplylist",
                        "bid": state.bid,
                        "tid": reference.tid,
                        "pid": state.reply_cursor,
                        "num": 20,
                    },
                    wait=wait,
                    jitter=jitter,
                    request_count=request_count,
                    referer=(
                        f"{SINA_GUBA_BASE_URL}view_"
                        f"{state.bid}_{reference.tid}.html"
                    ),
                )
                request_count += 1
                received_replies = parse_sina_reply_payload(
                    cast(dict[str, object], reply_response.json()),
                    code=job.code,
                    bid=state.bid,
                    tid=reference.tid,
                    collected_at=now,
                )
                if not received_replies:
                    raise ValueError("新浪股吧回复分页提前结束")
                replies = sorted(
                    received_replies,
                    key=lambda reply: reply.published_at,
                    reverse=True,
                )
                remaining_topic_replies = max(
                    0,
                    max_replies_per_topic - state.reply_seen_count,
                )
                selected_replies = replies[:remaining_topic_replies]
                for reply in selected_replies:
                    reply_date = reply.published_at.date()
                    if job.start_date <= reply_date <= job.end_date:
                        if reply.content_id in known_content_ids:
                            continue
                        if (
                            state.selected_content_count
                            >= max_contents_per_job
                        ):
                            state.sample_capped = True
                            global_limit_reached = True
                            listing_complete = True
                            break
                        contents_by_date.setdefault(reply_date, []).append(reply)
                        known_content_ids.add(reply.content_id)
                        state.selected_content_count += 1
                        if (
                            state.selected_content_count
                            >= max_contents_per_job
                        ):
                            state.sample_capped = True
                            global_limit_reached = True
                            listing_complete = True
                            break
                state.reply_seen_count += len(selected_replies)
                state.reply_cursor = (
                    received_replies[-1].content_id.rsplit(":", 1)[-1]
                )
                if global_limit_reached:
                    update_public_opinion_job(
                        path,
                        job_id=job.id,
                        status="running",
                        cursor=state.model_dump_json(),
                        updated_at=datetime.now(now.tzinfo),
                    )
                    break
                topic_reply_capped = (
                    len(selected_replies) < len(replies)
                    or (
                        state.reply_seen_count >= max_replies_per_topic
                        and reference.reply_count > state.reply_seen_count
                    )
                )
                if topic_reply_capped:
                    state.sample_capped = True
                    _advance_sina_topic(state)
                    update_public_opinion_job(
                        path,
                        job_id=job.id,
                        status="running",
                        cursor=state.model_dump_json(),
                        updated_at=datetime.now(now.tzinfo),
                    )
                    continue
                reached_before_range = any(
                    reply.published_at.date() < job.start_date
                    for reply in replies
                )
                if (
                    reached_before_range
                    or state.reply_seen_count >= reference.reply_count
                ):
                    _advance_sina_topic(state)
                update_public_opinion_job(
                    path,
                    job_id=job.id,
                    status="running",
                    cursor=state.model_dump_json(),
                    updated_at=datetime.now(now.tzinfo),
                )
            if global_limit_reached:
                break
            if state.topic_index < len(current_page.references):
                break
            if state.finish_after_page:
                listing_complete = True
                break
            if (
                state.next_relate_value is None
                or state.next_relate_tid is None
            ):
                error_summary = "新浪股吧主题分页缺少下一页游标"
                break
            if (
                state.next_relate_value == state.page_relate_value
                and state.next_relate_tid == state.page_relate_tid
            ):
                error_summary = "新浪股吧主题分页游标没有前进"
                break
            state = SinaCollectionCursor(
                bid=state.bid,
                phase="topics",
                topic_relate_value=state.next_relate_value,
                topic_relate_tid=state.next_relate_tid,
                topic_pages_processed=state.topic_pages_processed,
                selected_content_count=state.selected_content_count,
                sample_capped=state.sample_capped,
                sample_limit=state.sample_limit,
            )
            current_page = None
    except (httpx.HTTPError, ValueError, KeyError) as error:
        error_summary = str(error)

    try:
        _save_sina_partial_contents(
            path,
            code=job.code,
            contents_by_date=contents_by_date,
            collected_at=now,
            classifier=classifier,
        )
    except DeepSeekOpinionError as error:
        return update_public_opinion_job(
            path,
            job_id=job.id,
            status="failed",
            cursor=original_cursor,
            updated_at=datetime.now(now.tzinfo),
            error_summary=str(error),
        )
    collection_complete = listing_complete and error_summary is None
    if collection_complete:
        current_date = job.start_date
        while current_date <= job.end_date:
            rebuild_public_opinion_day(
                path,
                platform="sina",
                code=job.code,
                actual_date=current_date,
                collection_complete=True,
                collected_at=now,
                rules_version=PROMPT_VERSION,
                sample_capped=state.sample_capped,
                sample_limit=state.sample_limit if state.sample_capped else None,
            )
            current_date += timedelta(days=1)
    if collection_complete:
        return update_public_opinion_job(
            path,
            job_id=job.id,
            status="succeeded",
            cursor=state.model_dump_json(),
            updated_at=datetime.now(now.tzinfo),
        )
    if error_summary is not None:
        return update_public_opinion_job(
            path,
            job_id=job.id,
            status="blocked",
            cursor=state.model_dump_json(),
            updated_at=datetime.now(now.tzinfo),
            error_summary=error_summary,
        )
    return update_public_opinion_job(
        path,
        job_id=job.id,
        status="pending",
        cursor=state.model_dump_json(),
        updated_at=datetime.now(now.tzinfo),
        error_summary=None,
    )


def _load_sina_cursor(raw_cursor: str | None) -> SinaCollectionCursor:
    if raw_cursor is None or not raw_cursor.lstrip().startswith("{"):
        return SinaCollectionCursor()
    try:
        return SinaCollectionCursor.model_validate_json(raw_cursor)
    except ValueError:
        return SinaCollectionCursor()


def _fetch_sina_topic_page(
    client: httpx.Client,
    *,
    code: str,
    market: str,
    bid: str,
    relate_value: int | None,
    relate_tid: str | None,
    collected_at: datetime,
    wait: Callable[[float], None],
    jitter: Callable[[], float],
    request_count: int,
) -> SinaTopicPage:
    params: dict[str, str | int] = {
        "s": "h5bar",
        "bid": bid,
        "num": 50,
    }
    if relate_value is not None and relate_tid is not None:
        params["relate_value"] = relate_value
        params["relate_tid"] = relate_tid
    response = _guarded_get_with_params(
        client,
        SINA_GUBA_API_URL,
        params=params,
        wait=wait,
        jitter=jitter,
        request_count=request_count,
        referer=f"{SINA_GUBA_BASE_URL}list_{market}{code}.html",
    )
    page = parse_sina_topic_payload(
        cast(dict[str, object], response.json()),
        code=code,
        bid=bid,
        collected_at=collected_at,
    )
    return page.model_copy(
        update={
            "references": sorted(
                page.references,
                key=lambda reference: reference.published_at,
                reverse=True,
            )
        }
    )


def _advance_sina_topic(state: SinaCollectionCursor) -> None:
    state.topic_index += 1
    state.reply_cursor = ""
    state.reply_seen_count = 0


def _save_sina_partial_contents(
    path: Path,
    *,
    code: str,
    contents_by_date: dict[date, list[OpinionContent]],
    collected_at: datetime,
    classifier: OpinionBatchClassifier,
) -> None:
    for actual_date, contents in contents_by_date.items():
        for offset in range(0, len(contents), 50):
            batch = contents[offset : offset + 50]
            cached = read_cached_model_classifications(
                path,
                contents=batch,
                model=DEEPSEEK_MODEL,
                prompt_version=PROMPT_VERSION,
            )
            missing = [
                content
                for content in batch
                if content.content_id not in cached
            ]
            classifications = {
                **cached,
                **classifier.classify(missing),
            }
            aggregate = aggregate_public_opinion(
                batch,
                classifications=classifications,
                actual_date=actual_date,
                platform="sina",
                code=code,
                collection_complete=False,
                rules_version=PROMPT_VERSION,
            )
            save_public_opinion_day(
                path,
                contents=batch,
                classifications=classifications,
                aggregate=aggregate,
                collected_at=collected_at,
            )
        rebuild_public_opinion_day(
            path,
            platform="sina",
            code=code,
            actual_date=actual_date,
            collection_complete=False,
            collected_at=collected_at,
            rules_version=PROMPT_VERSION,
        )


def run_tonghuashun_collection_job(
    path: Path,
    *,
    job_id: int,
    now: datetime,
) -> PublicOpinionCollectionJob:
    job = read_public_opinion_job(path, job_id)
    if job.platform != "tonghuashun":
        raise ValueError("该执行器只处理同花顺任务")
    return update_public_opinion_job(
        path,
        job_id=job.id,
        status="blocked",
        cursor=job.cursor,
        updated_at=now,
        error_summary=(
            "同花顺热股榜已确认存在，但尚未找到可验证、完整、无需登录的"
            "评论列表接口；本任务不伪造舆论内容"
        ),
    )


def run_public_opinion_job(
    path: Path,
    *,
    job_id: int,
    now: datetime,
    client: httpx.Client,
) -> PublicOpinionCollectionJob:
    job = read_public_opinion_job(path, job_id)
    if job.platform == "eastmoney":
        return run_eastmoney_collection_job(
            path,
            job_id=job_id,
            client=client,
            now=now,
        )
    if job.platform == "sina":
        api_key = get_deepseek_api_key()
        if not api_key:
            return update_public_opinion_job(
                path,
                job_id=job.id,
                status="failed",
                updated_at=now,
                cursor=job.cursor,
                error_summary="未配置 DEEPSEEK_API_KEY，未执行舆论采集",
            )
        return run_sina_collection_job(
            path,
            job_id=job_id,
            client=client,
            classifier=DeepSeekOpinionClassifier(
                api_key=api_key,
                client=client,
            ),
            now=now,
            max_requests_per_run=3,
        )
    if job.platform == "xueqiu":
        return update_public_opinion_job(
            path,
            job_id=job_id,
            status="blocked",
            updated_at=now,
            cursor=job.cursor,
            error_summary="该受限来源已从舆论采集工程移除",
        )
    return run_tonghuashun_collection_job(
        path,
        job_id=job_id,
        now=now,
    )


def _guarded_get(
    client: httpx.Client,
    url: str,
    *,
    wait: Callable[[float], None],
    jitter: Callable[[], float],
    request_count: int,
) -> httpx.Response:
    if request_count > 0:
        wait(5.0 + jitter())
    response = client.get(
        url,
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return response


def _guarded_get_with_params(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str | int],
    wait: Callable[[float], None],
    jitter: Callable[[], float],
    request_count: int,
    referer: str,
) -> httpx.Response:
    if request_count > 0:
        wait(5.0 + jitter())
    response = client.get(
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0",
        },
    )
    response.raise_for_status()
    return response


def _eastmoney_list_url(code: str, page: int) -> str:
    suffix = "" if page == 1 else f"_{page}"
    return f"{EASTMONEY_BASE_URL}list,{code}{suffix}.html"


def _cursor_page(cursor: str | None) -> int:
    if cursor is None:
        return 1
    if not cursor.startswith("page:"):
        raise ValueError("东方财富任务游标格式无效")
    return max(1, int(cursor.removeprefix("page:")))


def _sina_market(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "2", "3")):
        return "sz"
    if code.startswith(("4", "8", "9")):
        return "bj"
    raise ValueError("新浪股吧暂不支持该股票代码市场")
