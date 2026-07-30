from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace

from pytest import MonkeyPatch

import fourseasquant.scheduled_pipeline as pipeline
from fourseasquant.automation import AutomationOutcome
from fourseasquant.fundamental_automation import FundamentalAutomationOutcome
from fourseasquant.fundamental_lynch_automation import LynchAutomationOutcome


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
            notification_due=True,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=date(2026, 7, 24),
            stage="complete",
            reason="该交易日林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda market_status, fundamental, _lynch, _strategy: notified.append(
            (market_status, fundamental)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notified[0][0] == "skipped"
    assert notified[0][1] is not None
    assert notified[0][1].stage == "capital_actions"


def test_combined_scheduler_defers_intermediate_market_failure_notification(
    monkeypatch: MonkeyPatch,
) -> None:
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="failed",
            target_date=date(2026, 7, 24),
            reason="自动任务失败",
            notification_status="not_sent",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == []


def test_combined_scheduler_notifies_the_final_market_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="failed",
            target_date=date(2026, 7, 24),
            reason="自动任务失败",
            notification_status="sent",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == ["sent"]


def test_combined_scheduler_defers_intermediate_fundamental_failure_notification(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="failed",
            target_date=target_date,
            stage="capital_actions",
            reason="资本行为采集失败",
            notification_due=False,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == []


def test_lynch_success_does_not_expose_a_deferred_fundamental_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="failed",
            target_date=target_date,
            stage="capital_actions",
            reason="资本行为采集失败",
            notification_due=False,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="succeeded",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == []


def test_scheduled_market_continuation_defers_early_capital_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    fundamental_calls: list[dict[str, object]] = []
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="succeeded",
            target_date=target_date,
            reason="日频行情已发布",
        ),
    )

    def run_fundamental(
        **kwargs: object,
    ) -> FundamentalAutomationOutcome:
        fundamental_calls.append(kwargs)
        return FundamentalAutomationOutcome(
            status="failed",
            target_date=target_date,
            stage="capital_actions",
            reason="资本行为采集失败",
            notification_due=False,
        )

    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        run_fundamental,
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert fundamental_calls == [
        {
            "target_date": target_date,
            "force": False,
            "ignore_schedule": True,
        }
    ]
    assert notifications == []


def test_lynch_failure_remains_visible_with_deferred_fundamental_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="failed",
            target_date=target_date,
            stage="capital_actions",
            reason="资本行为采集失败",
            notification_due=False,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="failed",
            target_date=target_date,
            stage="complete",
            reason="林奇结果计算失败",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "MacOSNotifier",
        lambda: SimpleNamespace(
            send=lambda title, message: notifications.append((title, message))
        ),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == [
        (
            "Fourseasquant 林奇数据更新失败",
            "2026-07-24 · 林奇结果计算失败",
        )
    ]


def test_combined_scheduler_continues_fundamentals_after_market_just_succeeded(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    fundamental_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="succeeded",
            target_date=target_date,
            reason="日频行情已发布",
        ),
    )

    def run_fundamental(
        **kwargs: object,
    ) -> FundamentalAutomationOutcome:
        fundamental_calls.append(kwargs)
        return FundamentalAutomationOutcome(
            status="succeeded",
            target_date=target_date,
            stage="complete",
            reason="资本行为已发布",
        )

    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        run_fundamental,
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "next_core_strategy_automation_date",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 0
    assert fundamental_calls == [
        {
            "force": False,
            "ignore_schedule": True,
            "target_date": target_date,
        }
    ]


def test_combined_scheduler_advances_core_strategy_after_fundamentals(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="资本行为已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    strategy_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline,
        "next_core_strategy_automation_date",
        lambda *_args, **_kwargs: target_date,
    )

    def advance_strategy(
        *_args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        strategy_calls.append(kwargs)
        return SimpleNamespace(
            state="finalized",
            model_dump=lambda **_: {"state": "finalized"},
        )

    monkeypatch.setattr(
        pipeline,
        "advance_core_strategy_automation",
        advance_strategy,
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 0
    assert strategy_calls[0]["target_date"] == target_date
    assert strategy_calls[0]["force_prepare"] is True


def test_combined_scheduler_does_not_advance_strategy_when_fundamentals_wait(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="skipped",
            target_date=target_date,
            reason="该交易日已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="schedule",
            reason="等待下一次自动重试",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    strategy_calls: list[date] = []
    notifications: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "_advance_core_strategy",
        lambda target: strategy_calls.append(target),
    )
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda *_args: notifications.append("sent"),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 0
    assert strategy_calls == []
    assert notifications == []


def test_combined_scheduler_notifies_failure_when_core_strategy_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    target_date = date(2026, 7, 24)
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_task",
        lambda **_kwargs: AutomationOutcome(
            status="succeeded",
            target_date=target_date,
            reason="日频行情已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_fundamental_update",
        lambda **_kwargs: FundamentalAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="资本行为已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="skipped",
            target_date=target_date,
            stage="complete",
            reason="林奇结果已发布",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "next_core_strategy_automation_date",
        lambda *_args, **_kwargs: target_date,
    )
    monkeypatch.setattr(
        pipeline,
        "advance_core_strategy_automation",
        lambda *_args, **_kwargs: SimpleNamespace(
            target_date=target_date,
            state="failed",
            reason="核心策略计算失败",
            model_dump=lambda **_: {
                "state": "failed",
                "reason": "核心策略计算失败",
            },
        ),
    )
    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pipeline,
        "MacOSNotifier",
        lambda: SimpleNamespace(
            send=lambda title, message: notifications.append((title, message))
        ),
    )
    monkeypatch.setattr(sys, "argv", ["scheduled-pipeline", "run-scheduled"])

    exit_code = pipeline.main()

    assert exit_code == 1
    assert notifications == [
        (
            "Fourseasquant 核心策略更新失败",
            "2026-07-24 · 核心策略计算失败",
        )
    ]


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
    monkeypatch.setattr(
        pipeline,
        "run_scheduled_lynch_update",
        lambda **_kwargs: LynchAutomationOutcome(
            status="succeeded",
            target_date=date(2026, 6, 30),
            stage="complete",
            reason="林奇补跑完成",
        ),
    )
    strategy_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        pipeline,
        "next_core_strategy_automation_date",
        lambda *_args, **_kwargs: date(2026, 6, 30),
    )

    def advance_strategy(
        *_args: object,
        **kwargs: object,
    ) -> SimpleNamespace:
        strategy_calls.append(kwargs)
        return SimpleNamespace(
            state="account_uninitialized",
            model_dump=lambda **_: {"state": "account_uninitialized"},
        )

    monkeypatch.setattr(
        pipeline,
        "advance_core_strategy_automation",
        advance_strategy,
    )
    notified: list[tuple[str, FundamentalAutomationOutcome | None]] = []
    monkeypatch.setattr(
        pipeline,
        "_notify_combined",
        lambda market_status, fundamental, _lynch, _strategy: notified.append(
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
            "notify_failure_immediately": True,
        }
    ]
    assert strategy_calls[0]["target_date"] == date(2026, 6, 30)
    assert strategy_calls[0]["force_prepare"] is True
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
