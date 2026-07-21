from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import pytest
from fourseasquant.backfill import (
    BackfillAlreadyRunning,
    BackfillRequest,
    execute_backfill,
    validate_backfill_range,
)
from fourseasquant.database import initialize_database, latest_snapshot
from fourseasquant.settings import SettingsUpdate, save_settings


def test_backfill_rejects_unknown_calendar_years_and_future_dates() -> None:
    with pytest.raises(ValueError, match="2023"):
        validate_backfill_range(date(2022, 12, 30), date(2023, 1, 3))
    beijing_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with pytest.raises(ValueError, match="未来"):
        validate_backfill_range(beijing_today, beijing_today + timedelta(days=1))
    with pytest.raises(ValueError, match="2026"):
        validate_backfill_range(date(2026, 12, 31), date(2027, 1, 4))


def test_backfill_uses_one_settings_snapshot_for_the_whole_batch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "frozen-settings.db"
    initialize_database(database)
    def change_settings_after_first_day(completed: int) -> None:
        if completed == 1:
            save_settings(
                database,
                SettingsUpdate(
                    auto_update_time="16:30",
                    benchmark="中证 500",
                    data_adapter="simulation_conservative",
                    new_stock_exclusion_days=15,
                ),
            )
    execute_backfill(
        BackfillRequest(start_date=date(2026, 7, 17), end_date=date(2026, 7, 21)),
        path=database,
        after_each=change_settings_after_first_day,
    )

    for target in (date(2026, 7, 17), date(2026, 7, 20), date(2026, 7, 21)):
        snapshot = latest_snapshot(database, target)
        assert snapshot is not None
        assert json.loads(snapshot.payload_json)["source"] == "simulation"


def test_only_one_backfill_batch_can_run_at_a_time(tmp_path: Path) -> None:
    database = tmp_path / "locked.db"
    initialize_database(database)
    first_day_started = Event()
    release_first_batch = Event()

    def pause_first_batch(_: int) -> None:
        first_day_started.set()
        release_first_batch.wait(timeout=2)

    first_batch = Thread(
        target=execute_backfill,
        kwargs={
            "request": BackfillRequest(
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
            ),
            "path": database,
            "after_each": pause_first_batch,
        },
    )
    first_batch.start()
    assert first_day_started.wait(timeout=2)
    with pytest.raises(BackfillAlreadyRunning):
        execute_backfill(
            BackfillRequest(
                start_date=date(2026, 7, 20),
                end_date=date(2026, 7, 21),
            ),
            path=database,
        )
    release_first_batch.set()
    first_batch.join(timeout=2)
    assert not first_batch.is_alive()
