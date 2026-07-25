from __future__ import annotations

import argparse
import json
import sys

from fourseasquant.automation import (
    MacOSNotifier,
    RecordingNotifier,
    run_scheduled_task,
    run_startup_catchup,
)
from fourseasquant.fundamental_automation import (
    FundamentalAutomationOutcome,
    run_scheduled_fundamental_update,
)
from fourseasquant.fundamental_lynch_automation import (
    LynchAutomationOutcome,
    run_scheduled_lynch_update,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fourseasquant 行情、策略与基本面联合自动任务"
    )
    parser.add_argument(
        "command",
        choices=("run-scheduled", "run-now", "catch-up"),
    )
    arguments = parser.parse_args()
    if arguments.command == "catch-up":
        notifications = RecordingNotifier()
        market = run_startup_catchup(notifier=notifications)
        market_allows_fundamental = market.status == "succeeded" or (
            market.status == "skipped"
            and market.reason.startswith("最近 ")
            and market.reason.endswith("日内没有缺失交易日")
        )
        catchup_fundamental = (
            run_scheduled_fundamental_update(
                target_date=market.target_date,
                ignore_schedule=True,
            )
            if market_allows_fundamental
            else None
        )
        catchup_lynch = (
            run_scheduled_lynch_update(target_date=market.target_date)
            if market_allows_fundamental
            else None
        )
        should_notify = market.status in {"succeeded", "failed"} or (
            catchup_fundamental is not None
            and catchup_fundamental.status in {"succeeded", "failed"}
        ) or (
            catchup_lynch is not None
            and catchup_lynch.status in {"succeeded", "failed"}
        )
        if should_notify:
            try:
                _notify_combined(
                    market.status, catchup_fundamental, catchup_lynch
                )
            except Exception as error:
                print(
                    f"联合任务通知失败：{type(error).__name__}: {error}",
                    file=sys.stderr,
                )
        print(
            json.dumps(
                {
                    "market": market.model_dump(mode="json"),
                    "fundamental": (
                        catchup_fundamental.model_dump(mode="json")
                        if catchup_fundamental is not None
                        else None
                    ),
                    "lynch": (
                        catchup_lynch.model_dump(mode="json")
                        if catchup_lynch is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return (
            1
            if market.status == "failed"
            or (
                catchup_fundamental is not None
                and catchup_fundamental.status == "failed"
            )
            or (
                catchup_lynch is not None
                and catchup_lynch.status == "failed"
            )
            else 0
        )

    notifications = RecordingNotifier()
    force = arguments.command == "run-now"
    market = run_scheduled_task(
        force=force,
        notifier=notifications,
    )
    fundamental: FundamentalAutomationOutcome | None = None
    lynch: LynchAutomationOutcome | None = None
    market_allows_fundamental = market.status == "succeeded" or (
        market.status == "skipped" and market.reason == "该交易日已发布"
    )
    if market_allows_fundamental:
        fundamental = run_scheduled_fundamental_update(force=force)
        lynch = run_scheduled_lynch_update(
            target_date=market.target_date,
            force=force,
        )

    failed = market.status == "failed" or (
        fundamental is not None and fundamental.status == "failed"
    ) or (
        lynch is not None and lynch.status == "failed"
    )
    should_notify = market.status in {"succeeded", "failed"} or (
        fundamental is not None
        and fundamental.status in {"succeeded", "failed"}
    ) or (
        lynch is not None and lynch.status in {"succeeded", "failed"}
    )
    if should_notify:
        try:
            _notify_combined(market.status, fundamental, lynch)
        except Exception as error:
            print(
                f"联合任务通知失败：{type(error).__name__}: {error}",
                file=sys.stderr,
            )
    print(
        json.dumps(
            {
                "market": market.model_dump(mode="json"),
                "fundamental": (
                    fundamental.model_dump(mode="json")
                    if fundamental is not None
                    else None
                ),
                "lynch": (
                    lynch.model_dump(mode="json")
                    if lynch is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if failed else 0


def _notify_combined(
    market_status: str,
    fundamental: FundamentalAutomationOutcome | None,
    lynch: LynchAutomationOutcome | None = None,
) -> None:
    notifier = MacOSNotifier()
    if market_status == "failed":
        notifier.send(
            "Fourseasquant 更新失败",
            "日频行情或策略任务失败，请查看网站状态与本机日志。",
        )
        return
    if fundamental is not None and fundamental.status == "failed":
        notifier.send(
            "Fourseasquant 基本面更新失败",
            f"{fundamental.target_date.isoformat()} · {fundamental.reason}",
        )
        return
    if lynch is not None and lynch.status == "failed":
        notifier.send(
            "Fourseasquant 林奇数据更新失败",
            f"{lynch.target_date.isoformat()} · {lynch.reason}",
        )
        return
    target_date = (
        fundamental.target_date.isoformat()
        if fundamental is not None
        else "今日"
    )
    notifier.send(
        "Fourseasquant 更新成功",
        f"{target_date} 日频结果、资本行为与林奇数据完整发布。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
