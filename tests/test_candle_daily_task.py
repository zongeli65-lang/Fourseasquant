from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import fourseasquant.candle_daily_task as candle_task
from fourseasquant.daily_snapshots import TaskRunResponse


ACTUAL_DATE = date(2026, 7, 29)
BEIJING = ZoneInfo("Asia/Shanghai")


def _successful_task() -> TaskRunResponse:
    now = datetime(2026, 7, 29, 16, 30, tzinfo=BEIJING)
    return TaskRunResponse(
        id=1,
        trigger_method="manual",
        target_date=ACTUAL_DATE,
        started_at=now,
        finished_at=now,
        stage="completed",
        stage_label="完成",
        status="succeeded",
    )


def test_successful_candle_task_prepares_core_strategy_after_market_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "daily.db"
    prepared_dates: list[date] = []
    monkeypatch.setattr(
        candle_task,
        "execute_daily_task",
        lambda *_, **__: _successful_task(),
    )
    monkeypatch.setattr(
        candle_task,
        "refresh_market_environment",
        lambda *_, **__: SimpleNamespace(actual_data_date=ACTUAL_DATE),
    )

    def fake_prepare(*_: object, **kwargs: object) -> None:
        requested_date = kwargs["requested_date"]
        assert isinstance(requested_date, date)
        prepared_dates.append(requested_date)

    monkeypatch.setattr(
        candle_task,
        "prepare_core_strategy_day",
        fake_prepare,
    )

    result = candle_task.execute_daily_task_with_candles(
        ACTUAL_DATE,
        path=database,
    )

    assert result.status == "succeeded"
    assert prepared_dates == [ACTUAL_DATE]


def test_market_environment_failure_does_not_prepare_core_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "daily.db"
    prepare_called = False
    monkeypatch.setattr(
        candle_task,
        "execute_daily_task",
        lambda *_, **__: _successful_task(),
    )

    def broken_environment(*_: object, **__: object) -> None:
        raise RuntimeError("市场环境失败")

    def fake_prepare(*_: object, **__: object) -> None:
        nonlocal prepare_called
        prepare_called = True

    monkeypatch.setattr(
        candle_task,
        "refresh_market_environment",
        broken_environment,
    )
    monkeypatch.setattr(
        candle_task,
        "prepare_core_strategy_day",
        fake_prepare,
    )

    result = candle_task.execute_daily_task_with_candles(
        ACTUAL_DATE,
        path=database,
    )

    assert result.status == "succeeded"
    assert prepare_called is False
