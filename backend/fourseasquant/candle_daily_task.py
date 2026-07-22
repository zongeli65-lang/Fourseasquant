from __future__ import annotations

from datetime import date
from pathlib import Path

from fourseasquant.candle_refresh import refresh_one_year_candles
from fourseasquant.daily_snapshots import (
    PreparedMarket,
    TaskRunResponse,
    TaskTrigger,
    execute_daily_task,
    prepare_simulated_market,
)
from fourseasquant.database import database_path


def execute_daily_task_with_candles(
    target_date: date,
    *,
    path: Path | None = None,
    trigger_method: TaskTrigger = "manual",
) -> TaskRunResponse:
    selected_path = path or database_path()

    def prepare_market(run_date: date, run_path: Path) -> PreparedMarket:
        refresh_one_year_candles(
            run_path,
            requested_end_date=run_date,
        )
        return prepare_simulated_market(run_date, run_path)

    return execute_daily_task(
        target_date,
        path=selected_path,
        market_stage_factory=prepare_market,
        trigger_method=trigger_method,
        propagate_unexpected=False,
    )
