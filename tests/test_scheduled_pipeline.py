from __future__ import annotations

import sys
from datetime import date

from pytest import MonkeyPatch

import fourseasquant.scheduled_pipeline as pipeline
from fourseasquant.automation import AutomationOutcome
from fourseasquant.fundamental_automation import FundamentalAutomationOutcome


def test_combined_scheduler_runs_fundamentals_after_market_is_published(
    monkeypatch: MonkeyPatch,
) -> None:
    notified: list[tuple[str, FundamentalAutomationOutcome | None]] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=date(2026, 7, 24),
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="failed",
            target_date=date(2026, 7, 24),
            stage="capital_actions",
            reason="资本行为采集或完整性校验失败",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda market_status, fundamental: notified.append(
            (market_status, fundamental)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notified[0][0] == "skipped"
    assert notified[0][1] is not None
    assert notified[0][1].stage == "capital_actions"


def test_startup_catchup_continues_with_the_same_fundamental_target_date(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline,
        "run_startup_catchup",
        lambda **_kwargs: AutomationOutcome(
            status="succeeded",
            target_date=date(2026, 6, 30),
            reason="启动补跑完成",
        ),
    )

    def run_fundamental(
        **kwargs: object,
    ) -> FundamentalAutomationOutcome:
        calls.append(kwargs)
        return FundamentalAutomationOutcome(
            status="succeeded",
            target_date=date(2026, 6, 30),
            stage="complete",
            reason="基本面补跑完成",
        )

    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        run_fundamental,
    )
    notified: list[tuple[str, FundamentalAutomationOutcome | None]] = []
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda market_status, fundamental: notified.append(
            (market_status, fundamental)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "catch-up"])

    exit_code = pipeline.main()

    assert exit_code == 0
    assert calls == [
        {
            "target_date": date(2026, 6, 30),
            "ignore_schedule": True,
        }
    ]
    assert notified[0][0] == "succeeded"


def test_startup_catchup_does_not_run_fundamentals_while_market_is_running(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline,
        "run_startup_catchup",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=date(2026, 6, 30),
            reason="该交易日任务已在运行或发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "catch-up"])

    exit_code = pipeline.main()

    assert exit_code == 0
    assert calls == []
