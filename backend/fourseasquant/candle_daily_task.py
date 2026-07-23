from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from fourseasquant.akshare_history import HISTORY_QFQ_SOURCE
from fourseasquant.candle_refresh import refresh_one_year_candles
from fourseasquant.daily_snapshots import (
    PreparedMarket,
    TaskRunResponse,
    TaskTrigger,
    execute_daily_task,
    prepare_simulated_market,
)
from fourseasquant.database import TechnicalScorePublicationRow, database_path
from fourseasquant.technical_scoring import (
    ALGORITHM_VERSION,
    score_technical_history,
)


def execute_daily_task_with_candles(
    target_date: date,
    *,
    path: Path | None = None,
    trigger_method: TaskTrigger = "manual",
) -> TaskRunResponse:
    selected_path = path or database_path()

    def prepare_market(run_date: date, run_path: Path) -> PreparedMarket:
        refreshed = refresh_one_year_candles(
            run_path,
            requested_end_date=run_date,
        )
        qfq_source = f"{HISTORY_QFQ_SOURCE}:{run_date.isoformat()}"
        staged = score_technical_history(
            run_path,
            official_start=_one_year_start(refreshed.actual_data_date),
            official_end=refreshed.actual_data_date,
            qfq_source=qfq_source,
            version=ALGORITHM_VERSION,
            activate=False,
        )
        publication = TechnicalScorePublicationRow(
            version=staged.version,
            official_start=staged.official_start,
            official_end=staged.official_end,
            qfq_source=staged.qfq_source,
            symbol_count=staged.symbol_count,
            score_count=staged.score_count,
            published_at=staged.published_at,
        )
        return replace(
            prepare_simulated_market(run_date, run_path),
            technical_publication=publication,
        )

    return execute_daily_task(
        target_date,
        path=selected_path,
        market_stage_factory=prepare_market,
        trigger_method=trigger_method,
        propagate_unexpected=False,
    )


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)
