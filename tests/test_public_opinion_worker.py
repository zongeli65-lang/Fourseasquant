from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from fourseasquant.database import initialize_database
from fourseasquant.public_opinion_repository import (
    JobStatus,
    PublicOpinionCollectionJob,
    publish_strategy_opinion_targets,
    schedule_automatic_collection_jobs,
    update_public_opinion_job,
)
from fourseasquant.public_opinion_worker import run_public_opinion_batch


BEIJING = ZoneInfo("Asia/Shanghai")


def test_batch_worker_round_robins_pending_slices_without_browser(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public-opinion-worker.db"
    initialize_database(path)
    now = datetime(2026, 7, 27, 12, tzinfo=BEIJING)
    publish_strategy_opinion_targets(
        path,
        actual_date=date(2026, 7, 27),
        strategy_version="core-strategy-v1",
        codes=["000001", "600000"],
        published_at=now,
    )
    jobs = schedule_automatic_collection_jobs(
        path,
        actual_date=date(2026, 7, 27),
        now=now,
    )
    update_public_opinion_job(
        path,
        job_id=jobs[0].id,
        status="running",
        updated_at=now,
        cursor="interrupted-slice",
    )
    attempts: dict[int, int] = {}
    order: list[int] = []

    def fake_run_job(
        database: Path,
        *,
        job_id: int,
        now: datetime,
        client: httpx.Client,
    ) -> PublicOpinionCollectionJob:
        del client
        order.append(job_id)
        attempts[job_id] = attempts.get(job_id, 0) + 1
        status: JobStatus = (
            "pending"
            if job_id == jobs[0].id and attempts[job_id] == 1
            else "succeeded"
        )
        return update_public_opinion_job(
            database,
            job_id=job_id,
            status=status,
            updated_at=now,
            cursor="slice" if status == "pending" else "done",
        )

    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200)
    )) as client:
        result = run_public_opinion_batch(
            path,
            client=client,
            stop_requested=lambda: False,
            run_job=fake_run_job,
            max_slices=3,
        )

    assert order == [jobs[0].id, jobs[1].id, jobs[0].id]
    assert result.slices_processed == 3
    assert result.succeeded == 2
    assert result.remaining == 0
