from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from fourseasquant.database import initialize_database
from fourseasquant.public_opinion import (
    ModelOpinionClassification,
    OpinionContent,
    aggregate_public_opinion,
)
from fourseasquant.public_opinion_collection import (
    collect_eastmoney_hot_discovery,
    run_eastmoney_collection_job,
    run_sina_collection_job,
)
from fourseasquant.public_opinion_deepseek import DeepSeekOpinionError
from fourseasquant.public_opinion_repository import (
    add_to_opinion_watchlist,
    create_manual_collection_jobs,
    list_public_opinion_jobs,
    read_public_opinion_universe,
    read_public_opinion_overview,
    read_public_opinion_window,
    save_public_opinion_day,
)


BEIJING = ZoneInfo("Asia/Shanghai")


class FakeOpinionClassifier:
    def classify(
        self,
        contents: list[OpinionContent],
    ) -> dict[str, ModelOpinionClassification]:
        return {
            content.content_id: ModelOpinionClassification(
                content_id=content.content_id,
                label=(
                    "unfavorable"
                    if "亏损" in content.text or "利空" in content.text
                    else "favorable"
                ),
                confidence=0.95,
                model="deepseek-v4-pro",
                prompt_version="public-opinion-deepseek-v1",
            )
            for content in contents
        }


class FailingOpinionClassifier:
    def classify(
        self,
        contents: list[OpinionContent],
    ) -> dict[str, ModelOpinionClassification]:
        del contents
        raise DeepSeekOpinionError("模拟云端分类失败")


def test_eastmoney_hot_discovery_fetches_complete_public_list_and_updates_universe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hot-discovery.db"
    initialize_database(path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        )
        return httpx.Response(
            200,
            json={
                "status": 0,
                "data": [
                    {"sc": "SZ000001", "rk": 1},
                    {"sc": "SH600000", "rk": 2},
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = collect_eastmoney_hot_discovery(
            path,
            actual_date=date(2026, 7, 26),
            collected_at=datetime(2026, 7, 26, 18, 0, tzinfo=BEIJING),
            name_by_code={"000001": "平安银行", "600000": "浦发银行"},
            expected_count=2,
            client=client,
        )

    assert result.platform == "eastmoney"
    assert result.status == "succeeded"
    assert result.stock_count == 2
    assert [item.code for item in read_public_opinion_universe(
        path,
        as_of_date=date(2026, 7, 26),
    )] == ["000001", "600000"]


def test_eastmoney_job_collects_topics_to_published_platform_day(
    tmp_path: Path,
) -> None:
    path = tmp_path / "eastmoney-job.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, 0, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    job = create_manual_collection_jobs(
        path,
        platform="eastmoney",
        code="000001",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 7, 26),
        now=now,
    )[0]
    rows = "".join(
        f"""
        <tr class="listitem">
          <td><div class="reply">0</div></td>
          <td><div class="title"><a data-postid="{index}"
            data-posttype="0" href="/news,000001,{index}.html">利好主题{index}</a></div></td>
          <td><div class="update">07-26 18:00</div></td>
        </tr>
        """
        for index in range(1, 11)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list,000001.html":
            return httpx.Response(200, text=f"<table>{rows}</table>")
        if request.url.path == "/list,000001_2.html":
            return httpx.Response(200, text="<table></table>")
        post_id = request.url.path.rsplit(",", 1)[-1].removesuffix(".html")
        return httpx.Response(
            200,
            text=f"""
            <script>var article={{
              "post_id":"{post_id}","post_type":0,
              "post_title":"明确利好",
              "post_content":"<p>盈利增长</p>",
              "post_publish_time":"2026-07-26 18:00:00",
              "post_like_count":0,"post_comment_count":0
            }};</script>
            """,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_eastmoney_collection_job(
            path,
            job_id=job.id,
            client=client,
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
        )

    stored_job = list_public_opinion_jobs(path)[0]
    overview = read_public_opinion_overview(path)
    assert result.status == "succeeded"
    assert stored_job.status == "succeeded"
    assert stored_job.cursor == "page:2"
    assert overview[0].eastmoney is not None
    assert overview[0].eastmoney.direction_status == "published"
    assert overview[0].eastmoney.direction == "favorable"


def test_eastmoney_job_blocks_direction_when_public_reply_endpoint_is_decoy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "eastmoney-reply-blocked.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, 0, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    job = create_manual_collection_jobs(
        path,
        platform="eastmoney",
        code="000001",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 7, 26),
        now=now,
    )[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list,000001.html":
            return httpx.Response(
                200,
                text="""
                <tr class="listitem">
                  <td><div class="reply">1</div></td>
                  <td><div class="title"><a data-postid="1"
                    data-posttype="0" href="/news,000001,1.html">主题</a></div></td>
                  <td><div class="update">07-26 18:00</div></td>
                </tr>
                <tr class="listitem">
                  <td><div class="reply">0</div></td>
                  <td><div class="title"><a data-postid="2"
                    data-posttype="0" href="/news,000001,2.html">主题二</a></div></td>
                  <td><div class="update">07-26 18:01</div></td>
                </tr>
                """,
            )
        if request.url.path == "/list,000001_2.html":
            return httpx.Response(200, text="<table></table>")
        if request.url.path.startswith("/news,000001,"):
            post_id = request.url.path.rsplit(",", 1)[-1].removesuffix(".html")
            return httpx.Response(
                200,
                text=f"""
                <script>var article={{"post_id":"{post_id}","post_type":0,
                  "post_title":"明确利好","post_content":"盈利增长",
                  "post_publish_time":"2026-07-26 18:00:00",
                  "post_like_count":0}};</script>
                """,
            )
        return httpx.Response(
            200,
            json={"re": True, "result": [{"security": "0$000001$123"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_eastmoney_collection_job(
            path,
            job_id=job.id,
            client=client,
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
        )

    overview = read_public_opinion_overview(path)
    assert result.status == "blocked"
    assert result.cursor == "page:2"
    assert "非评论数据" in (result.error_summary or "")
    assert overview[0].eastmoney is not None
    assert overview[0].eastmoney.content_count == 2
    assert overview[0].eastmoney.direction_status == "collecting"


def test_sina_job_collects_topics_replies_and_likes_to_complete_platform_day(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-job.db"
    initialize_database(path)
    now = datetime(2026, 7, 26, 20, 0, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="000001",
        name="平安银行",
        now=now,
    )
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="000001",
        start_date=date(2026, 7, 26),
        end_date=date(2026, 7, 26),
        now=now,
    )[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list_sz000001.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"平安银行","bid":"477","isLogin":false,
                  "stockMarket":"sz","stockCode":"000001"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            if request.url.params.get("relate_value"):
                return httpx.Response(
                    200,
                    json={
                        "bid": "477",
                        "data": {"topThreadList": [], "threads": []},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "bid": "477",
                    "data": {
                        "topThreadList": [],
                        "threads": [
                            {
                                "tid": "806853",
                                "title": "平安银行讨论",
                                "content": "盈利增长，明确利好",
                                "long_content": "",
                                "timestamp": 1785032551,
                                "reply": 1,
                                "like": 6,
                            }
                        ],
                    },
                },
            )
        assert request.url.params.get("s") == "h5thread"
        assert request.url.params.get("a") == "getreplylist"
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "data": [
                            {
                                "pid": "4",
                                "content": "盈利改善，继续增持",
                                "ctimestamp": 1785036151,
                                "good": 3,
                            }
                        ]
                    },
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
        )

    overview = read_public_opinion_overview(path)
    assert result.status == "succeeded"
    assert overview[0].sina is not None
    assert overview[0].sina.collection_complete is True
    assert overview[0].sina.content_count == 2
    assert overview[0].sina.valid_count == 2
    assert overview[0].sina.direction_status == "insufficient_sample"


def test_sina_job_stops_reply_paging_before_requested_date_range(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-large-history.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    add_to_opinion_watchlist(
        path,
        code="688825",
        name="胜科纳米",
        now=now,
    )
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="688825",
        start_date=date(2026, 7, 25),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]
    reply_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reply_requests
        if request.url.path == "/list_sh688825.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"胜科纳米","bid":"320860","isLogin":false,
                  "stockMarket":"sh","stockCode":"688825"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            if request.url.params.get("relate_value"):
                return httpx.Response(
                    200,
                    json={
                        "bid": "320860",
                        "data": {"topThreadList": [], "threads": []},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "bid": "320860",
                    "data": {
                        "topThreadList": [],
                        "threads": [{
                            "tid": "3899",
                            "title": "目标日期内主题",
                            "content": "业绩讨论",
                            "long_content": "",
                            "timestamp": int(datetime(
                                2026, 7, 25, 10, tzinfo=BEIJING
                            ).timestamp()),
                            "reply": 10_000,
                            "like": 1,
                        }],
                    },
                },
            )
        reply_requests += 1
        if reply_requests > 2:
            raise AssertionError("翻到目标日期之前后仍继续请求历史回复")
        published_at = (
            datetime(2026, 7, 26, 10, tzinfo=BEIJING)
            if reply_requests == 1
            else datetime(2026, 7, 24, 10, tzinfo=BEIJING)
        )
        pid = "100" if reply_requests == 1 else "90"
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "data": [{
                            "pid": pid,
                            "content": "盈利增长",
                            "ctimestamp": int(published_at.timestamp()),
                            "good": 0,
                        }]
                    },
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
        )

    window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=3,
    )
    assert result.status == "succeeded"
    assert reply_requests == 2
    assert window.content_count == 2


def test_sina_job_stops_and_marks_capped_at_per_stock_window_limit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-content-cap.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="688825",
        start_date=date(2026, 7, 25),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list_sh688825.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"N长鑫","bid":"320860","isLogin":false,
                  "stockMarket":"sh","stockCode":"688825"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            return httpx.Response(
                200,
                json={
                    "bid": "320860",
                    "data": {
                        "topThreadList": [],
                        "threads": [
                            {
                                "tid": str(5_000 - index),
                                "title": f"主题 {index}",
                                "content": "公司经营讨论",
                                "long_content": "",
                                "timestamp": int(datetime(
                                    2026, 7, 27, 11, index,
                                    tzinfo=BEIJING,
                                ).timestamp()),
                                "reply": 0,
                                "like": index,
                            }
                            for index in range(5)
                        ],
                    },
                },
            )
        raise AssertionError(f"不应请求 {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_contents_per_job=3,
        )

    window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert result.status == "succeeded"
    assert result.sample_capped is True
    assert result.sample_limit == 3
    assert window.content_count == 3
    assert window.completed_day_count == 1
    with sqlite3.connect(path) as connection:
        selected_ids = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT content_id
                FROM public_opinion_content_references
                WHERE prompt_version = 'public-opinion-deepseek-v1'
                """
            )
        }
    assert selected_ids == {"4996", "4997", "4998"}


def test_sina_job_counts_existing_current_version_rows_toward_cap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-existing-content-cap.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="688825",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]
    existing = [
        OpinionContent(
            platform="sina",
            content_id=f"existing-{index}",
            code="688825",
            url=f"https://example.test/{index}",
            published_at=datetime(
                2026, 7, 27, 11, index, tzinfo=BEIJING
            ),
            collected_at=now,
            likes=0,
            content_kind="topic",
            source_type="user_original",
            text="已有新版内容",
        )
        for index in range(2)
    ]
    existing_classifications = FakeOpinionClassifier().classify(existing)
    save_public_opinion_day(
        path,
        contents=existing,
        classifications=existing_classifications,
        aggregate=aggregate_public_opinion(
            existing,
            classifications=existing_classifications,
            actual_date=date(2026, 7, 27),
            platform="sina",
            code="688825",
            collection_complete=False,
            rules_version="public-opinion-deepseek-v1",
        ),
        collected_at=now,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list_sh688825.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"N长鑫","bid":"320860","isLogin":false,
                  "stockMarket":"sh","stockCode":"688825"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            return httpx.Response(
                200,
                json={
                    "bid": "320860",
                    "data": {
                        "topThreadList": [],
                        "threads": [
                            {
                                "tid": tid,
                                "title": f"新主题 {index}",
                                "content": "新增内容",
                                "long_content": "",
                                "timestamp": int(datetime(
                                    2026, 7, 27, 10, 2 - index,
                                    tzinfo=BEIJING,
                                ).timestamp()),
                                "reply": 0,
                                "like": 0,
                            }
                            for index, tid in enumerate(
                                ("existing-0", "6000", "5999")
                            )
                        ],
                    },
                },
            )
        raise AssertionError(f"不应请求 {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_contents_per_job=3,
        )

    window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert result.sample_capped is True
    assert window.content_count == 3


def test_sina_hot_stock_yields_after_request_slice_and_saves_partial_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-request-slice.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="688825",
        start_date=date(2026, 7, 25),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]
    reply_request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reply_request_count
        if request.url.path == "/list_sh688825.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"胜科纳米","bid":"320860","isLogin":false,
                  "stockMarket":"sh","stockCode":"688825"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            return httpx.Response(
                200,
                json={
                    "bid": "320860",
                    "data": {
                        "topThreadList": [],
                        "threads": [{
                            "tid": "3899",
                            "title": "高热度主题",
                            "content": "盈利增长",
                            "long_content": "",
                            "timestamp": int(datetime(
                                2026, 7, 27, 10, tzinfo=BEIJING
                            ).timestamp()),
                            "reply": 10_000,
                            "like": 1,
                        }],
                    },
                },
            )
        reply_request_count += 1
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "data": [{
                            "pid": str(10_000 - reply_request_count),
                            "content": "继续增长",
                            "ctimestamp": int(datetime(
                                2026, 7, 27, 10, reply_request_count,
                                tzinfo=BEIJING,
                            ).timestamp()),
                            "good": 0,
                        }]
                    },
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_requests_per_run=3,
            max_contents_per_job=3,
        )

    stored = list_public_opinion_jobs(path)[0]
    window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert result.status == "pending"
    assert stored.cursor is not None
    assert stored.cursor.startswith("{")
    assert reply_request_count == 1
    assert window.content_count == 2
    assert window.direction_status == "collecting"

    first_cursor = stored.cursor
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resumed = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_requests_per_run=3,
            max_contents_per_job=3,
        )
    resumed_window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert resumed.status == "succeeded"
    assert resumed.sample_capped is True
    assert resumed.sample_limit == 3
    assert resumed.cursor != first_cursor
    assert resumed_window.content_count == 3


def test_sina_job_caps_replies_from_one_topic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-topic-reply-cap.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="688825",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]
    reply_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reply_requests
        if request.url.path == "/list_sh688825.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"N长鑫","bid":"320860","isLogin":false,
                  "stockMarket":"sh","stockCode":"688825"
                };</script>
                """,
            )
        if request.url.params.get("s") == "h5bar":
            if request.url.params.get("relate_value"):
                return httpx.Response(
                    200,
                    json={
                        "bid": "320860",
                        "data": {"topThreadList": [], "threads": []},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "bid": "320860",
                    "data": {
                        "topThreadList": [],
                        "threads": [{
                            "tid": "3899",
                            "title": "高热度主题",
                            "content": "公司经营讨论",
                            "long_content": "",
                            "timestamp": int(datetime(
                                2026, 7, 27, 10, tzinfo=BEIJING
                            ).timestamp()),
                            "reply": 10_000,
                            "like": 1,
                        }],
                    },
                },
            )
        reply_requests += 1
        if reply_requests > 1:
            raise AssertionError("单主题达到回复上限后仍继续翻页")
        return httpx.Response(
            200,
            json={
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "data": [
                            {
                                "pid": str(100 - index),
                                "content": f"回复 {index}",
                                "ctimestamp": int(datetime(
                                    2026, 7, 27, 9, index,
                                    tzinfo=BEIJING,
                                ).timestamp()),
                                "good": index,
                            }
                            for index in range(3)
                        ]
                    },
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FakeOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_contents_per_job=10,
            max_replies_per_topic=2,
        )

    window = read_public_opinion_window(
        path,
        platform="sina",
        code="688825",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert result.status == "succeeded"
    assert result.sample_capped is True
    assert window.content_count == 3
    assert reply_requests == 1


def test_sina_job_does_not_advance_cursor_when_model_classification_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sina-model-failure.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    job = create_manual_collection_jobs(
        path,
        platform="sina",
        code="600519",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 27),
        now=now,
    )[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list_sh600519.html":
            return httpx.Response(
                200,
                text="""
                <script>var PAGE_CONFIG = {
                  "barName":"贵州茅台","bid":"240","isLogin":false,
                  "stockMarket":"sh","stockCode":"600519"
                };</script>
                """,
            )
        return httpx.Response(
            200,
            json={
                "bid": "240",
                "data": {
                    "topThreadList": [],
                    "threads": [{
                        "tid": "730023",
                        "title": "茅台讨论",
                        "content": "继续看涨",
                        "long_content": None,
                        "timestamp": int(now.timestamp()),
                        "reply": 0,
                        "like": 1,
                    }],
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_sina_collection_job(
            path,
            job_id=job.id,
            client=client,
            classifier=FailingOpinionClassifier(),
            now=now,
            wait=lambda _: None,
            jitter=lambda: 0.0,
            max_requests_per_run=2,
        )

    window = read_public_opinion_window(
        path,
        platform="sina",
        code="600519",
        end_date=date(2026, 7, 27),
        days=1,
    )
    assert result.status == "failed"
    assert result.cursor is None
    assert result.error_summary == "模拟云端分类失败"
    assert window.content_count == 0
