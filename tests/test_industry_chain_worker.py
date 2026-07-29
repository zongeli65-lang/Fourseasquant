from __future__ import annotations

import json
import sqlite3
from threading import Event
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.industry_chain.cninfo_source import CninfoPollResult
from fourseasquant.industry_chain.control import (
    enqueue_source_event_hunt,
    record_runtime_activity,
    read_hunting_requests,
    read_runtime_status,
    set_hunting_enabled,
    submit_manual_hunt,
)
from fourseasquant.industry_chain.discovery import (
    DiscoveryItem,
    append_discovery_items,
)
from fourseasquant.industry_chain.ollama_runtime import OllamaStructuredRuntime
from fourseasquant.industry_chain.announcement_triage import TriageDecision
from fourseasquant.industry_chain.retention import CleanupResult
from fourseasquant.industry_chain.triage_repository import (
    read_untriaged_discovery_ids,
)
from fourseasquant.industry_chain.worker import (
    IndustryChainWorker,
    _enqueue_actionable,
    _run_heartbeat_loop,
)


BEIJING = ZoneInfo("Asia/Shanghai")


def test_dedicated_heartbeat_continues_independently_of_worker_pass(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-heartbeat.db"
    stop_event = Event()
    calls: list[Path] = []

    def recorder(path: Path, **_: object) -> None:
        calls.append(path)
        stop_event.set()

    _run_heartbeat_loop(
        path=database,
        stop_event=stop_event,
        interval_seconds=0.01,
        recorder=recorder,
    )

    assert calls == [database]


def test_actionable_news_is_queued_as_separate_event_hunts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "separate-events.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    decisions = (
        TriageDecision(
            discovery_id="cls-news:order",
            action="investigate",
            suspected_event_type="major_order",
            priority=1,
            reason="公司重大订单",
        ),
        TriageDecision(
            discovery_id="cls-news:shortage",
            action="investigate",
            suspected_event_type="supply_contraction",
            priority=1,
            reason="上游供应中断",
        ),
    )
    append_discovery_items(
        database,
        (
            DiscoveryItem(
                discovery_id="cls-news:order",
                source_id="cls-news",
                external_id="order",
                security_code="000001",
                security_name="测试甲",
                headline="测试甲签订重大供货订单",
                published_at=now,
                collected_at=now,
                source_url="https://example.test/order",
                attachment_url=None,
                payload={"brief": "测试甲取得客户订单并将开始交付"},
            ),
            DiscoveryItem(
                discovery_id="cls-news:shortage",
                source_id="cls-news",
                external_id="shortage",
                security_code="000002",
                security_name="测试乙",
                headline="测试乙上游供应中断",
                published_at=now,
                collected_at=now,
                source_url="https://example.test/shortage",
                attachment_url=None,
                payload={"brief": "上游工厂事故停产导致原料供应中断"},
            ),
        ),
    )

    queued = _enqueue_actionable(database, decisions, now)
    requests = read_hunting_requests(database)

    assert queued == 2
    payloads = [json.loads(request.trigger_content) for request in requests]
    assert {tuple(payload["discovery_ids"]) for payload in payloads} == {
        ("cls-news:order",),
        ("cls-news:shortage",),
    }


def test_worker_runs_hourly_cleanup_even_when_hunting_is_disabled(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-cleanup.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    calls: list[datetime] = []

    def cleanup_runner(path: Path, *, now: datetime) -> CleanupResult:
        assert path == database
        calls.append(now)
        return CleanupResult(
            cleanup_run_id="cleanup-test",
            discovery_cutoff=now - timedelta(days=7),
            run_cutoff=now - timedelta(days=30),
            deleted_counts={},
            completed_at=now,
        )

    worker = IndustryChainWorker(
        database,
        cleanup_runner=cleanup_runner,
        source_event_researcher=None,
        media_pollers=(),
    )

    first = worker.run_once(now=now)
    second = worker.run_once(now=now + timedelta(minutes=59))
    third = worker.run_once(now=now + timedelta(hours=1))

    assert first.enabled is False
    assert second.enabled is False
    assert third.enabled is False
    assert calls == [now, now + timedelta(hours=1)]
    assert (
        read_runtime_status(
            database,
            now=now + timedelta(hours=1),
        ).last_cleanup_at
        == now + timedelta(hours=1)
    )


def test_source_event_success_records_latest_model_run(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = tmp_path / "worker-source-event-model-time.db"
    initialize_database(database)
    now = datetime.now(BEIJING) - timedelta(minutes=5)
    set_hunting_enabled(database, enabled=True, now=now)
    record_runtime_activity(database, now=now, polled=True)
    item = DiscoveryItem(
        discovery_id="cls-news:model-time",
        source_id="cls-news",
        external_id="model-time",
        security_code=None,
        security_name=None,
        headline="原料供应中断",
        published_at=now,
        collected_at=now,
        source_url="https://www.cls.cn/detail/model-time",
        attachment_url=None,
        payload={"publication_time_known": True},
    )
    append_discovery_items(database, (item,))
    request = enqueue_source_event_hunt(
        database,
        discovery_ids=(item.discovery_id,),
        event_types=("supply_contraction",),
        priority=1,
        now=now,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "fourseasquant.industry_chain.worker.analyze_source_event",
        lambda *_args, **_kwargs: SimpleNamespace(publication=None),
    )

    IndustryChainWorker(
        database,
        source_event_researcher=None,
        media_pollers=(),
    ).run_once(now=now)

    completed = next(
        item
        for item in read_hunting_requests(database)
        if item.request_id == request.request_id
    )
    status = read_runtime_status(database)
    assert completed.status == "succeeded"
    assert status.last_model_run_at is not None
    assert status.last_model_run_at > now


def test_second_source_event_in_same_pass_uses_fresh_time(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    database = tmp_path / "worker-fresh-second-event-time.db"
    initialize_database(database)
    now = datetime.now(BEIJING) - timedelta(minutes=5)
    set_hunting_enabled(database, enabled=True, now=now)
    record_runtime_activity(database, now=now, polled=True)
    item = DiscoveryItem(
        discovery_id="cls-news:fresh-second",
        source_id="cls-news",
        external_id="fresh-second",
        security_code=None,
        security_name=None,
        headline="人员任免公告",
        published_at=now,
        collected_at=now,
        source_url="https://www.cls.cn/detail/fresh-second",
        attachment_url=None,
        payload={"publication_time_known": True},
    )
    append_discovery_items(database, (item,))

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
                        "decisions": [
                            {
                                "discovery_id": item.discovery_id,
                                "action": "ignore",
                                "suspected_event_type": "unrelated",
                                "priority": 3,
                                "reason": "与产业供需无关",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    observed_times: list[datetime] = []
    clock_values = iter(
        (
            now,
            now + timedelta(minutes=5),
            now + timedelta(minutes=5, seconds=1),
        )
    )
    worker = IndustryChainWorker(
        database,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        source_event_researcher=None,
        media_pollers=(),
        clock=lambda: next(clock_values),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        worker,
        "_process_one_source_event",
        observed_times.append,
    )

    worker.run_once()

    assert len(observed_times) == 2
    assert observed_times[0] == now
    assert observed_times[1] > now + timedelta(minutes=4)


def test_worker_respects_switch_and_connects_poll_triage_and_queue(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    calls = 0

    def poller(path: Path, **_: object) -> CninfoPollResult:
        nonlocal calls
        calls += 1
        item = DiscoveryItem(
            discovery_id="cninfo:order-1",
            source_id="cninfo",
            external_id="order-1",
            security_code="000001",
            security_name="测试公司",
            headline="签订重大供货订单",
            published_at=now,
            collected_at=now,
            source_url="https://www.cninfo.com.cn/order-1",
            attachment_url=None,
            payload={"headline": "签订重大供货订单"},
        )
        inserted = append_discovery_items(path, (item,))
        return CninfoPollResult(
            pages_read=1,
            announcements_seen=1,
            eligible_seen=1,
            inserted_ids=inserted.inserted_ids,
            checkpoint_external_id="order-1",
            checkpoint_reached=False,
        )

    def model_transport(_: str, payload: object) -> dict[str, object]:
        del payload
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": "cninfo:order-1",
                                "action": "investigate",
                                "suspected_event_type": "major_order",
                                "priority": 1,
                                "reason": "重大供货订单需核验主营占比",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
            "total_duration": 1,
        }

    worker = IndustryChainWorker(
        database,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        poller=poller,
        eligible_code_loader=lambda _: frozenset({"000001"}),
        media_pollers=(),
        source_event_researcher=None,
    )

    disabled = worker.run_once(now=now, force_poll=True)
    assert disabled.enabled is False
    assert calls == 0

    set_hunting_enabled(database, enabled=True, now=now)
    completed = worker.run_once(now=now, force_poll=True)
    status = read_runtime_status(database, now=now)
    requests = read_hunting_requests(database)
    with sqlite3.connect(database) as connection:
        triage_count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_triage_runs"
        ).fetchone()

    assert completed.polled is True
    assert completed.inserted_count == 1
    assert completed.triaged_count == 1
    assert completed.queued_for_investigation == 1
    assert completed.error_summary is None
    assert calls == 1
    assert status.worker_online is True
    assert status.last_poll_at == now
    assert status.last_model_run_at is not None
    assert {request.trigger_method for request in requests} == {
        "resume_catchup",
        "new_evidence",
    }
    assert triage_count == (1,)


def test_worker_executes_manual_message_without_waiting_for_next_poll(
    tmp_path: Path,
) -> None:
    database = tmp_path / "manual-worker.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    set_hunting_enabled(database, enabled=True, now=now)
    record_runtime_activity(database, now=now, polled=True)
    submitted = submit_manual_hunt(
        database,
        trigger_type="message",
        content="海外铜矿因事故暂停生产，复产时间尚未确定。",
        now=now,
    )

    def model_transport(
        _: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        messages = payload["messages"]
        assert isinstance(messages, list)
        user_message = messages[1]
        assert isinstance(user_message, dict)
        rows = json.loads(str(user_message["content"]))
        discovery_id = rows[0]["discovery_id"]
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": discovery_id,
                                "action": "investigate",
                                "suspected_event_type": "supply_contraction",
                                "priority": 1,
                                "reason": "矿山停产可能造成供应收缩",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    worker = IndustryChainWorker(
        database,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        poller=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("三分钟内不应再次轮询")
        ),
        eligible_code_loader=lambda _: frozenset(),
        media_pollers=(),
        source_event_researcher=None,
    )
    result = worker.run_once(now=now.replace(minute=1))
    requests = read_hunting_requests(database)
    manual = next(
        request for request in requests if request.request_id == submitted.request_id
    )

    assert result.polled is False
    assert manual.status == "succeeded"
    assert any(request.trigger_method == "new_evidence" for request in requests)


def test_worker_resumes_discoveries_left_untriaged_by_previous_process(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-backlog.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    set_hunting_enabled(database, enabled=True, now=now)
    record_runtime_activity(database, now=now, polled=True)
    item = DiscoveryItem(
        discovery_id="cls-news:backlog-1",
        source_id="cls-news",
        external_id="backlog-1",
        security_code=None,
        security_name=None,
        headline="人员任免公告",
        published_at=now,
        collected_at=now,
        source_url="https://www.cls.cn/detail/backlog-1",
        attachment_url=None,
        payload={"published_at_known": True},
    )
    append_discovery_items(database, (item,))

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
                        "decisions": [
                            {
                                "discovery_id": item.discovery_id,
                                "action": "ignore",
                                "suspected_event_type": "unrelated",
                                "priority": 3,
                                "reason": "与产业供需无关",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    worker = IndustryChainWorker(
        database,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        poller=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("存在积压时本轮不应先联网轮询")
        ),
        eligible_code_loader=lambda _: frozenset(),
        media_pollers=(),
    )
    result = worker.run_once(now=now.replace(minute=1))

    with sqlite3.connect(database) as connection:
        triage_count = connection.execute(
            "SELECT COUNT(*) FROM industry_chain_triage_runs"
        ).fetchone()

    assert result.polled is False
    assert result.triaged_count == 1
    assert triage_count == (1,)


def test_untriaged_backlog_prioritizes_supply_demand_signal(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-priority.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    generic = DiscoveryItem(
        discovery_id="ths-discovery:generic",
        source_id="ths-discovery",
        external_id="generic",
        security_code=None,
        security_name=None,
        headline="市场资讯汇总",
        published_at=now,
        collected_at=now,
        source_url="https://news.10jqka.com.cn/generic",
        attachment_url=None,
        payload={
            "headline": "市场资讯汇总",
            "publication_time_known": False,
        },
    )
    supply_signal = DiscoveryItem(
        discovery_id="ths-discovery:supply",
        source_id="ths-discovery",
        external_id="supply",
        security_code=None,
        security_name=None,
        headline="芯片厂商要求暂停生产某型号产品",
        published_at=now,
        collected_at=now,
        source_url="https://news.10jqka.com.cn/supply",
        attachment_url=None,
        payload={
            "headline": "芯片厂商要求暂停生产某型号产品",
            "publication_time_known": False,
        },
    )
    append_discovery_items(database, (generic, supply_signal))

    pending = read_untriaged_discovery_ids(
        database,
        as_of_time=now,
        limit=1,
    )

    assert pending == (supply_signal.discovery_id,)


def test_switch_off_finishes_current_triage_then_pauses_next_step(
    tmp_path: Path,
) -> None:
    database = tmp_path / "worker-graceful-pause.db"
    initialize_database(database)
    now = datetime(2026, 7, 26, 12, 0, tzinfo=BEIJING)
    set_hunting_enabled(database, enabled=True, now=now)
    record_runtime_activity(database, now=now, polled=True)
    item = DiscoveryItem(
        discovery_id="cls-news:pause-after-current",
        source_id="cls-news",
        external_id="pause-after-current",
        security_code=None,
        security_name=None,
        headline="海外矿山暂停生产",
        published_at=now,
        collected_at=now,
        source_url="https://www.cls.cn/detail/pause-after-current",
        attachment_url=None,
        payload={
            "headline": "海外矿山暂停生产",
            "published_at_known": True,
        },
    )
    append_discovery_items(database, (item,))
    calls = 0

    def model_transport(
        _: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        nonlocal calls
        del payload
        calls += 1
        set_hunting_enabled(
            database,
            enabled=False,
            now=now.replace(second=10),
        )
        return {
            "model": "qwen3:14b",
            "message": {
                "content": json.dumps(
                    {
                        "decisions": [
                            {
                                "discovery_id": item.discovery_id,
                                "action": "investigate",
                                "suspected_event_type": "supply_contraction",
                                "priority": 1,
                                "reason": "矿山停产需要深挖",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    result = IndustryChainWorker(
        database,
        runtime=OllamaStructuredRuntime(transport=model_transport),
        poller=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("最近已轮询，不应再次联网")
        ),
        eligible_code_loader=lambda _: frozenset(),
        media_pollers=(),
    ).run_once(now=now.replace(minute=1))
    status = read_runtime_status(database, now=now.replace(minute=1))
    requests = read_hunting_requests(database)
    source_event = next(
        request for request in requests if request.trigger_type == "source_event"
    )

    assert calls == 1
    assert result.enabled is False
    assert result.triaged_count == 1
    assert status.enabled is False
    assert status.state == "paused"
    assert source_event.status == "paused"
