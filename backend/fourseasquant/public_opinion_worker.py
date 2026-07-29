from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from fourseasquant.public_opinion_collection import run_public_opinion_job
from fourseasquant.public_opinion_deepseek import (
    DEEPSEEK_MODEL,
    PROMPT_VERSION,
)
from fourseasquant.public_opinion_repository import (
    PublicOpinionCollectionJob,
    list_public_opinion_jobs,
    update_public_opinion_job,
)
from fourseasquant.public_opinion_secrets import get_deepseek_api_key


RunJob = Callable[..., PublicOpinionCollectionJob]


class PublicOpinionBatchResult(BaseModel):
    slices_processed: int
    succeeded: int
    blocked: int
    failed: int
    remaining: int
    stopped: bool


class PublicOpinionBatchStatus(PublicOpinionBatchResult):
    running: bool
    stop_requested: bool
    current_job_id: int | None
    current_code: str | None
    latest_end_date: date | None
    started_at: datetime | None
    updated_at: datetime | None
    error_summary: str | None
    classification_model: str = DEEPSEEK_MODEL
    prompt_version: str = PROMPT_VERSION
    classifier_configured: bool = False


def run_public_opinion_batch(
    path: Path,
    *,
    client: httpx.Client,
    stop_requested: Callable[[], bool],
    run_job: RunJob = run_public_opinion_job,
    on_progress: Callable[
        [int, int, int, int, int, PublicOpinionCollectionJob | None],
        None,
    ] | None = None,
    max_slices: int | None = None,
) -> PublicOpinionBatchResult:
    jobs = [
        job
        for job in list_public_opinion_jobs(path)
        if job.platform == "sina" and job.trigger == "automatic"
    ]
    if not jobs:
        return PublicOpinionBatchResult(
            slices_processed=0,
            succeeded=0,
            blocked=0,
            failed=0,
            remaining=0,
            stopped=False,
        )
    latest_end_date = max(job.end_date for job in jobs)
    recovered_jobs = []
    for job in jobs:
        if job.end_date == latest_end_date and job.status == "running":
            job = update_public_opinion_job(
                path,
                job_id=job.id,
                status="pending",
                updated_at=datetime.now().astimezone(),
                cursor=job.cursor,
                error_summary=None,
            )
        recovered_jobs.append(job)
    queue = deque(
        job
        for job in recovered_jobs
        if job.end_date == latest_end_date
        and job.status in {"pending", "failed"}
    )
    slices_processed = 0
    succeeded = 0
    blocked = 0
    failed = 0
    while queue and not stop_requested():
        if max_slices is not None and slices_processed >= max_slices:
            break
        job = queue.popleft()
        if on_progress is not None:
            on_progress(
                slices_processed,
                succeeded,
                blocked,
                failed,
                len(queue) + 1,
                job,
            )
        try:
            result = run_job(
                path,
                job_id=job.id,
                now=datetime.now().astimezone(),
                client=client,
            )
        except Exception as error:
            result = update_public_opinion_job(
                path,
                job_id=job.id,
                status="failed",
                updated_at=datetime.now().astimezone(),
                cursor=job.cursor,
                error_summary=f"后台轮转异常：{type(error).__name__}: {error}",
            )
        slices_processed += 1
        if result.status == "pending":
            queue.append(result)
        elif result.status == "succeeded":
            succeeded += 1
        elif result.status == "blocked":
            blocked += 1
        else:
            failed += 1
        if on_progress is not None:
            on_progress(
                slices_processed,
                succeeded,
                blocked,
                failed,
                len(queue),
                None,
            )
    return PublicOpinionBatchResult(
        slices_processed=slices_processed,
        succeeded=succeeded,
        blocked=blocked,
        failed=failed,
        remaining=len(queue),
        stopped=stop_requested(),
    )


class PublicOpinionBatchController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = _empty_status()

    def status(self) -> PublicOpinionBatchStatus:
        with self._lock:
            status = self._status.model_copy(deep=True)
        status.classifier_configured = bool(get_deepseek_api_key())
        return status

    def start(self, path: Path) -> PublicOpinionBatchStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._status.model_copy(deep=True)
            automatic_jobs = [
                job
                for job in list_public_opinion_jobs(path)
                if job.platform == "sina"
                and job.trigger == "automatic"
            ]
            latest_end_date = max(
                (job.end_date for job in automatic_jobs),
                default=None,
            )
            jobs = [
                job
                for job in automatic_jobs
                if job.end_date == latest_end_date
                and job.status in {"pending", "failed", "running"}
            ]
            now = datetime.now().astimezone()
            classifier_configured = bool(get_deepseek_api_key())
            self._stop_event = threading.Event()
            self._status = PublicOpinionBatchStatus(
                running=bool(jobs) and classifier_configured,
                stop_requested=False,
                current_job_id=None,
                current_code=None,
                latest_end_date=latest_end_date,
                started_at=now if jobs else None,
                updated_at=now,
                error_summary=(
                    None
                    if classifier_configured or not jobs
                    else "未配置 DEEPSEEK_API_KEY，批次未启动"
                ),
                slices_processed=0,
                succeeded=0,
                blocked=0,
                failed=0,
                remaining=len(jobs),
                stopped=False,
                classifier_configured=classifier_configured,
            )
            if not jobs or not classifier_configured:
                return self._status.model_copy(deep=True)
            self._thread = threading.Thread(
                target=self._run,
                args=(path,),
                name="public-opinion-batch",
                daemon=True,
            )
            self._thread.start()
            return self._status.model_copy(deep=True)

    def stop(self) -> PublicOpinionBatchStatus:
        self._stop_event.set()
        with self._lock:
            self._status.stop_requested = True
            self._status.updated_at = datetime.now().astimezone()
            return self._status.model_copy(deep=True)

    def _run(self, path: Path) -> None:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(120, connect=20),
                follow_redirects=True,
            ) as client:
                result = run_public_opinion_batch(
                    path,
                    client=client,
                    stop_requested=self._stop_event.is_set,
                    on_progress=self._record_progress,
                )
            with self._lock:
                self._status.slices_processed = result.slices_processed
                self._status.succeeded = result.succeeded
                self._status.blocked = result.blocked
                self._status.failed = result.failed
                self._status.remaining = result.remaining
                self._status.stopped = result.stopped
        except Exception as error:
            with self._lock:
                self._status.error_summary = (
                    f"{type(error).__name__}: {error}"
                )
        finally:
            with self._lock:
                self._status.running = False
                self._status.current_job_id = None
                self._status.current_code = None
                self._status.updated_at = datetime.now().astimezone()

    def _record_progress(
        self,
        slices_processed: int,
        succeeded: int,
        blocked: int,
        failed: int,
        remaining: int,
        current: PublicOpinionCollectionJob | None,
    ) -> None:
        with self._lock:
            self._status.slices_processed = slices_processed
            self._status.succeeded = succeeded
            self._status.blocked = blocked
            self._status.failed = failed
            self._status.remaining = remaining
            self._status.current_job_id = None if current is None else current.id
            self._status.current_code = None if current is None else current.code
            self._status.updated_at = datetime.now().astimezone()


def _empty_status() -> PublicOpinionBatchStatus:
    return PublicOpinionBatchStatus(
        running=False,
        stop_requested=False,
        current_job_id=None,
        current_code=None,
        latest_end_date=None,
        started_at=None,
        updated_at=None,
        error_summary=None,
        slices_processed=0,
        succeeded=0,
        blocked=0,
        failed=0,
        remaining=0,
        stopped=False,
    )
