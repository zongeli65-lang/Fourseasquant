from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

from .announcement_triage import TriageDecision, triage_announcements
from .cninfo_source import CninfoPollResult, poll_cninfo_announcements
from .cls_source import poll_cls_telegraphs
from .control import (
    claim_next_manual_hunt,
    claim_next_source_event_hunt,
    complete_hunting_pause,
    finish_catchup_requests,
    finish_hunting_request,
    read_runtime_status,
    record_cleanup_completed,
    record_runtime_activity,
    record_worker_heartbeat,
    recover_interrupted_hunts,
)
from .discovery import read_discovery_items
from .fresh_queue import (
    cancel_low_value_queued_event_hunts,
    MAX_FRESH_TRIAGE_BACKLOG,
    prune_untriaged_news,
    read_fresh_news_funnel,
)
from .event_funnel import cluster_and_queue_actionable_events
from .deep_hunt import StaleDiscoveryError, analyze_source_event
from .deep_hunt import PROMPT_VERSION as DEEP_HUNT_PROMPT_VERSION
from .deep_hunt_audit import record_deep_hunt_audit
from .html_listing_source import poll_html_listing
from .local_data import LocalIndustryChainData
from .manual_hunt import ingest_manual_hunt
from .ollama_runtime import OllamaStructuredRuntime
from .notifications import notify_selection
from .public_search import (
    SourceEventResearchResult,
    execute_keyword_search,
    execute_source_event_research,
)
from .repository import IndustryChainRepository
from .retention import CleanupResult, cleanup_runtime_data
from .triage_repository import (
    read_unclustered_actionable_decisions,
    read_untriaged_discovery_ids,
    save_announcement_triage,
)


BEIJING = ZoneInfo("Asia/Shanghai")
MAX_TRIAGE_BATCH = 8
MAX_TRIAGE_BATCHES_PER_PASS = 1
EVENT_QUEUE_TARGET = 4
Poller = Callable[..., CninfoPollResult]
EligibleCodeLoader = Callable[[datetime], frozenset[str]]
MediaPoller = Callable[[Path, datetime], tuple[str, ...]]
SourceEventResearcher = Callable[..., SourceEventResearchResult]
CleanupRunner = Callable[..., CleanupResult]
HeartbeatRecorder = Callable[..., None]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class WorkerPassResult:
    enabled: bool
    polled: bool
    inserted_count: int
    triaged_count: int
    queued_for_investigation: int
    error_summary: str | None


class IndustryChainWorker:
    def __init__(
        self,
        path: Path,
        *,
        runtime: OllamaStructuredRuntime | None = None,
        poller: Poller = poll_cninfo_announcements,
        eligible_code_loader: EligibleCodeLoader | None = None,
        media_pollers: tuple[MediaPoller, ...] | None = None,
        source_event_researcher: SourceEventResearcher | None = (
            execute_source_event_research
        ),
        cleanup_runner: CleanupRunner = cleanup_runtime_data,
        clock: Clock | None = None,
    ) -> None:
        self._path = path
        self._runtime = runtime or OllamaStructuredRuntime()
        self._poller = poller
        self._eligible_code_loader = (
            eligible_code_loader or self._load_eligible_codes
        )
        self._source_event_researcher = source_event_researcher
        self._cleanup_runner = cleanup_runner
        self._clock = clock or (lambda: datetime.now(BEIJING))
        self._media_pollers = (
            media_pollers
            if media_pollers is not None
            else (
                _poll_cls,
                _poll_eastmoney,
                _poll_ths,
            )
        )

    def run_once(
        self,
        *,
        now: datetime | None = None,
        force_poll: bool = False,
    ) -> WorkerPassResult:
        live_clock = now is None
        current = (now or self._clock()).astimezone(BEIJING)
        status = read_runtime_status(self._path, now=current)
        record_worker_heartbeat(self._path, now=current)
        cleanup_error = self._cleanup_if_due(
            last_cleanup_at=status.last_cleanup_at,
            current=current,
        )
        if not status.enabled:
            complete_hunting_pause(self._path, now=current)
            return WorkerPassResult(
                False,
                False,
                0,
                0,
                0,
                cleanup_error,
            )
        self._process_one_manual(current)
        if self._finish_pause_if_requested(current):
            return WorkerPassResult(False, False, 0, 0, 0, None)
        cancel_low_value_queued_event_hunts(
            self._path,
            as_of_time=current,
        )
        self._process_one_source_event(current)
        if live_clock:
            current = self._clock().astimezone(BEIJING)
        if self._finish_pause_if_requested(current):
            return WorkerPassResult(False, False, 0, 0, 0, None)
        self._top_up_event_queue(current)
        funnel = read_fresh_news_funnel(
            self._path,
            as_of_time=current,
        )
        if funnel.pending_triage_count > MAX_FRESH_TRIAGE_BACKLOG:
            prune_untriaged_news(
                self._path,
                as_of_time=current,
            )
            funnel = read_fresh_news_funnel(
                self._path,
                as_of_time=current,
            )
        if funnel.pending_triage_count > 0:
            return self._process_backlog(
                current=current,
                polled=False,
                inserted_count=0,
                source_error=cleanup_error,
                live_clock=live_clock,
            )
        poll_due = force_poll or _poll_is_due(status.last_poll_at, current)
        if not poll_due:
            return self._process_backlog(
                current=current,
                polled=False,
                inserted_count=0,
                source_error=cleanup_error,
                live_clock=live_clock,
            )

        inserted_ids: list[str] = []
        errors: list[str] = [cleanup_error] if cleanup_error else []
        cninfo_succeeded = False
        try:
            eligible_codes = self._eligible_code_loader(current)
            start_date = (
                status.catchup_from.date()
                if status.catchup_from is not None
                else current.date()
            )
            cninfo_result = self._poller(
                self._path,
                eligible_codes=eligible_codes,
                start_date=start_date,
                end_date=current.date(),
                as_of_time=current,
            )
            inserted_ids.extend(cninfo_result.inserted_ids)
            cninfo_succeeded = True
        except Exception as error:
            errors.append(f"巨潮：{type(error).__name__}: {error}")
        for media_poller in self._media_pollers:
            try:
                inserted_ids.extend(media_poller(self._path, current))
            except Exception as error:
                errors.append(
                    f"{media_poller.__name__}：{type(error).__name__}: {error}"
                )
        error_summary = "；".join(errors)[:1000] or None
        record_runtime_activity(
            self._path,
            now=current,
            polled=True,
            error_summary=error_summary,
        )
        if cninfo_succeeded:
            finish_catchup_requests(self._path, now=current)
        if self._finish_pause_if_requested(current):
            return WorkerPassResult(
                False,
                True,
                len(inserted_ids),
                0,
                0,
                error_summary,
            )
        return self._process_backlog(
            current=current,
            polled=True,
            inserted_count=len(inserted_ids),
            source_error=error_summary,
            live_clock=live_clock,
        )

    def _cleanup_if_due(
        self,
        *,
        last_cleanup_at: datetime | None,
        current: datetime,
    ) -> str | None:
        if not _cleanup_is_due(last_cleanup_at, current):
            return None
        try:
            self._cleanup_runner(
                self._path,
                now=current,
            )
            record_cleanup_completed(self._path, now=current)
            return None
        except Exception as error:
            summary = f"定期清理：{type(error).__name__}: {error}"[:1000]
            record_runtime_activity(
                self._path,
                now=current,
                error_summary=summary,
            )
            return summary

    def _process_backlog(
        self,
        *,
        current: datetime,
        polled: bool,
        inserted_count: int,
        source_error: str | None,
        live_clock: bool,
    ) -> WorkerPassResult:
        backlog_ids = read_untriaged_discovery_ids(
            self._path,
            as_of_time=current,
            limit=MAX_TRIAGE_BATCH * MAX_TRIAGE_BATCHES_PER_PASS,
        )
        if not backlog_ids:
            return WorkerPassResult(
                True,
                polled,
                inserted_count,
                0,
                0,
                source_error,
            )
        try:
            triaged_count, queued_count = self._triage_inserted(
                backlog_ids,
                current=current,
            )
            if self._finish_pause_if_requested(current):
                return WorkerPassResult(
                    False,
                    polled,
                    inserted_count,
                    triaged_count,
                    queued_count,
                    source_error,
                )
            self._process_one_source_event(
                self._clock().astimezone(BEIJING)
                if live_clock
                else current
            )
            return WorkerPassResult(
                True,
                polled,
                inserted_count,
                triaged_count,
                queued_count,
                source_error,
            )
        except Exception as error:
            triage_error = f"{type(error).__name__}: {error}"[:1000]
            summary = (
                f"{source_error}；初筛：{triage_error}"
                if source_error
                else triage_error
            )[:1000]
            record_runtime_activity(
                self._path,
                now=datetime.now(BEIJING),
                error_summary=summary,
            )
            return WorkerPassResult(
                True,
                polled,
                inserted_count,
                0,
                0,
                summary,
            )

    def _finish_pause_if_requested(self, current: datetime) -> bool:
        status = read_runtime_status(self._path, now=current)
        if status.enabled:
            return False
        complete_hunting_pause(self._path, now=datetime.now(BEIJING))
        return True

    def _top_up_event_queue(self, current: datetime) -> int:
        status = read_runtime_status(self._path, now=current)
        capacity = max(
            0,
            EVENT_QUEUE_TARGET - status.queued_count - status.running_count,
        )
        if capacity == 0:
            return 0
        decisions = read_unclustered_actionable_decisions(
            self._path,
            as_of_time=current,
            limit=capacity,
        )
        if not decisions:
            return 0
        return _enqueue_actionable(self._path, decisions, current)

    def _process_one_manual(self, current: datetime) -> None:
        request = claim_next_manual_hunt(self._path, now=current)
        if request is None:
            return
        try:
            if request.trigger_type == "keyword":
                discovery_ids = execute_keyword_search(
                    self._path,
                    request=request,
                    now=current,
                    runtime=self._runtime,
                )
            else:
                discovery_ids = ingest_manual_hunt(
                    self._path,
                    request=request,
                    now=current,
                )
            if discovery_ids:
                self._triage_inserted(discovery_ids, current=current)
            completed_at = datetime.now(BEIJING)
            finish_hunting_request(
                self._path,
                request_id=request.request_id,
                succeeded=True,
                now=completed_at,
            )
            record_runtime_activity(
                self._path,
                now=completed_at,
                model_ran=True,
            )
        except Exception as error:
            summary = f"{type(error).__name__}: {error}"[:1000]
            finish_hunting_request(
                self._path,
                request_id=request.request_id,
                succeeded=False,
                now=datetime.now(BEIJING),
                error_summary=summary,
            )
            record_runtime_activity(
                self._path,
                now=datetime.now(BEIJING),
                error_summary=summary,
            )

    def _process_one_source_event(self, current: datetime) -> None:
        request = claim_next_source_event_hunt(self._path, now=current)
        if request is None:
            return
        try:
            research = (
                self._source_event_researcher(
                    self._path,
                    request=request,
                    now=current,
                    runtime=self._runtime,
                )
                if self._source_event_researcher is not None
                else None
            )
            result = analyze_source_event(
                self._path,
                request=request,
                runtime=self._runtime,
                now=current,
                additional_discovery_ids=(
                    research.discovery_ids if research is not None else ()
                ),
                eligible_company_codes=(
                    research.company_codes if research is not None else ()
                ),
            )
            if result.publication is not None and result.publication.inserted:
                snapshot = IndustryChainRepository(self._path).read_selection(
                    result.publication.selection_id,
                    selection_version=result.publication.selection_version,
                )
                if snapshot is not None:
                    notify_selection(
                        self._path,
                        snapshot=snapshot,
                        now=datetime.now(BEIJING),
                    )
            completed_at = datetime.now(BEIJING)
            finish_hunting_request(
                self._path,
                request_id=request.request_id,
                succeeded=True,
                now=completed_at,
            )
            record_runtime_activity(
                self._path,
                now=completed_at,
                model_ran=True,
            )
        except StaleDiscoveryError:
            finish_hunting_request(
                self._path,
                request_id=request.request_id,
                succeeded=True,
                now=datetime.now(BEIJING),
            )
        except Exception as error:
            summary = f"{type(error).__name__}: {error}"[:1000]
            completed_at = datetime.now(BEIJING)
            record_deep_hunt_audit(
                self._path,
                request_id=request.request_id,
                outcome="failed",
                decision=None,
                error_summary=summary,
                model=None,
                prompt_version=DEEP_HUNT_PROMPT_VERSION,
                started_at=request.started_at or current,
                completed_at=completed_at,
            )
            finish_hunting_request(
                self._path,
                request_id=request.request_id,
                succeeded=False,
                now=completed_at,
                error_summary=summary,
            )
            record_runtime_activity(
                self._path,
                now=datetime.now(BEIJING),
                error_summary=summary,
            )

    def _load_eligible_codes(self, now: datetime) -> frozenset[str]:
        snapshot = LocalIndustryChainData(self._path).eligible_universe(
            as_of_time=now
        )
        return frozenset(item.code for item in snapshot.securities)

    def _triage_inserted(
        self,
        discovery_ids: tuple[str, ...],
        *,
        current: datetime,
    ) -> tuple[int, int]:
        maximum = MAX_TRIAGE_BATCH * MAX_TRIAGE_BATCHES_PER_PASS
        items = read_discovery_items(self._path, discovery_ids[:maximum])
        triaged_count = 0
        queued_count = 0
        source_ids = tuple(dict.fromkeys(item.source_id for item in items))
        for source_id in source_ids:
            source_items = tuple(
                item for item in items if item.source_id == source_id
            )
            for offset in range(0, len(source_items), MAX_TRIAGE_BATCH):
                batch = source_items[offset : offset + MAX_TRIAGE_BATCH]
                started_at = datetime.now(BEIJING)
                result = triage_announcements(batch, runtime=self._runtime)
                completed_at = datetime.now(BEIJING)
                save_announcement_triage(
                    self._path,
                    items=batch,
                    result=result,
                    started_at=started_at,
                    completed_at=completed_at,
                )
                triaged_count += len(batch)
                actionable = tuple(
                    decision
                    for decision in result.batch.decisions
                    if decision.action in {"investigate", "observe"}
                )
                if actionable:
                    queued_count += _enqueue_actionable(
                        self._path,
                        actionable,
                        current,
                    )
                record_runtime_activity(
                    self._path,
                    now=completed_at,
                    model_ran=True,
                )
        return triaged_count, queued_count


def run_worker_forever(
    path: Path,
    *,
    interval_seconds: int = 30,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    if interval_seconds < 1:
        raise ValueError("后台心跳间隔必须大于零")
    recover_interrupted_hunts(
        path,
        now=datetime.now(BEIJING),
    )
    worker = IndustryChainWorker(path)
    should_stop = stop_requested or (lambda: False)
    heartbeat_stop = Event()
    heartbeat_thread = Thread(
        target=_run_heartbeat_loop,
        kwargs={
            "path": path,
            "stop_event": heartbeat_stop,
            "interval_seconds": interval_seconds,
        },
        name="industry-chain-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        while not should_stop():
            worker.run_once()
            for _ in range(interval_seconds):
                if should_stop():
                    return
                time.sleep(1)
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)


def _run_heartbeat_loop(
    *,
    path: Path,
    stop_event: Event,
    interval_seconds: float,
    recorder: HeartbeatRecorder = record_worker_heartbeat,
) -> None:
    while not stop_event.is_set():
        try:
            recorder(path, now=datetime.now(BEIJING))
        except sqlite3.OperationalError:
            # 主流程可能正处于极短的 SQLite 写事务；下一次心跳重试即可。
            pass
        stop_event.wait(interval_seconds)


def _poll_is_due(last_poll_at: datetime | None, now: datetime) -> bool:
    if last_poll_at is None:
        return True
    local = now.astimezone(BEIJING)
    active_hours = local.hour >= 7 and (
        local.hour < 23 or (local.hour == 23 and local.minute <= 30)
    )
    interval = timedelta(minutes=10 if active_hours else 30)
    return local - last_poll_at.astimezone(BEIJING) >= interval


def _cleanup_is_due(
    last_cleanup_at: datetime | None,
    now: datetime,
) -> bool:
    if last_cleanup_at is None:
        return True
    return now - last_cleanup_at.astimezone(BEIJING) >= timedelta(hours=1)


def _enqueue_actionable(
    path: Path,
    decisions: tuple[TriageDecision, ...],
    now: datetime,
) -> int:
    selected = tuple(sorted(
        decisions,
        key=lambda item: (
            item.priority,
            item.discovery_id,
        ),
    ))
    return cluster_and_queue_actionable_events(
        path,
        decisions=selected,
        now=now,
    ).queued_count


def _poll_cls(path: Path, now: datetime) -> tuple[str, ...]:
    return poll_cls_telegraphs(path, as_of_time=now).inserted_ids


def _poll_eastmoney(path: Path, now: datetime) -> tuple[str, ...]:
    return poll_html_listing(
        path,
        source_id="eastmoney-discovery",
        as_of_time=now,
    ).inserted_ids


def _poll_ths(path: Path, now: datetime) -> tuple[str, ...]:
    return poll_html_listing(
        path,
        source_id="ths-discovery",
        as_of_time=now,
    ).inserted_ids
