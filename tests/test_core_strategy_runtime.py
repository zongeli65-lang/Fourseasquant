from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import fourseasquant.core_strategy_runtime as runtime
from fourseasquant.core_strategy import (
    CORE_STRATEGY_VERSION,
    CandidatePipelineDecision,
)
from fourseasquant.core_strategy_adapters import CoreStrategyDailyInputs
from fourseasquant.core_strategy_orchestrator import (
    StrategyInvestigationPreparation,
)
from fourseasquant.core_strategy_repository import (
    CoreStrategyInputVersions,
    StrategyFundamentalTargetSnapshot,
)
from fourseasquant.public_opinion_repository import (
    StrategyOpinionTargetSnapshot,
    publish_strategy_opinion_targets,
    schedule_automatic_collection_jobs as schedule_opinion_jobs,
)


ACTUAL_DATE = date(2026, 7, 29)
VERSION = CORE_STRATEGY_VERSION
BEIJING = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 29, 16, 30, tzinfo=BEIJING)


def _preparation(
    opinion_codes: list[str] | None = None,
) -> StrategyInvestigationPreparation:
    daily = CoreStrategyDailyInputs(
        requested_date=ACTUAL_DATE,
        actual_date=ACTUAL_DATE,
        market_state="sideways",
        market_data_complete=True,
        corporate_actions_complete=True,
        technical_version="technical-v3",
        qfq_source="test-qfq",
        input_versions=CoreStrategyInputVersions(
            market_environment="market-environment-v1:test",
            technical_scores="technical-v3:test-qfq",
            industry_chain="industry-chain:empty",
        ),
        candidates=[],
        reasons=[],
    )
    pipeline = CandidatePipelineDecision(
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        candidates=[],
        fundamental_targets=[],
    )
    return StrategyInvestigationPreparation(
        daily_inputs=daily,
        candidate_pipeline=pipeline,
        opinion_targets=StrategyOpinionTargetSnapshot(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            codes=opinion_codes or [],
            published_at=NOW,
        ),
        fundamental_targets=StrategyFundamentalTargetSnapshot(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            targets=[],
            published_at=NOW,
        ),
    )


def test_runtime_prepares_idempotently_then_finalizes_empty_portfolio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime.db"
    runtime.initialize_core_strategy_account(
        database,
        initial_capital=100_000,
        initialized_at=NOW,
    )
    preparation_calls = 0
    schedule_calls: list[date] = []

    def fake_prepare(*_: object, **__: object) -> StrategyInvestigationPreparation:
        nonlocal preparation_calls
        preparation_calls += 1
        return _preparation()

    def fake_schedule(*_: object, **kwargs: object) -> list[object]:
        actual_date = kwargs["actual_date"]
        assert isinstance(actual_date, date)
        schedule_calls.append(actual_date)
        return []

    monkeypatch.setattr(
        runtime,
        "read_capital_action_status",
        lambda *_, **__: SimpleNamespace(status="ready"),
    )
    monkeypatch.setattr(
        runtime,
        "prepare_strategy_investigations",
        fake_prepare,
    )
    monkeypatch.setattr(
        runtime,
        "schedule_automatic_collection_jobs",
        fake_schedule,
    )

    prepared = runtime.prepare_core_strategy_day(
        database,
        requested_date=ACTUAL_DATE,
        prepared_at=NOW,
    )
    repeated = runtime.prepare_core_strategy_day(
        database,
        requested_date=ACTUAL_DATE,
        prepared_at=NOW,
    )
    finalized = runtime.finalize_core_strategy_day(
        database,
        actual_date=ACTUAL_DATE,
        finalized_at=NOW,
    )

    assert prepared.stage == "investigations_ready"
    assert repeated.stage == "investigations_ready"
    assert preparation_calls == 1
    assert schedule_calls == [ACTUAL_DATE, ACTUAL_DATE]
    assert finalized.stage == "finalized"
    assert finalized.order_count == 0
    assert finalized.holding_count == 0
    assert finalized.available_cash == 100_000
    assert finalized.net_asset_value == 100_000
    assert finalized.portfolio_publication_complete is True


def test_runtime_blocks_when_same_day_capital_actions_are_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-blocked.db"
    runtime.initialize_core_strategy_account(
        database,
        initial_capital=50_000,
        initialized_at=NOW,
    )
    monkeypatch.setattr(
        runtime,
        "read_capital_action_status",
        lambda *_, **__: SimpleNamespace(status="stale"),
    )

    status = runtime.prepare_core_strategy_day(
        database,
        requested_date=ACTUAL_DATE,
        prepared_at=NOW,
    )

    assert status.stage == "blocked_inputs"
    assert "公司行为数据" in status.message
    assert status.latest_attempt is not None
    assert status.latest_attempt.status == "blocked"


def test_account_initial_capital_cannot_change_silently(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-account.db"
    first = runtime.initialize_core_strategy_account(
        database,
        initial_capital=50_000,
        initialized_at=NOW,
    )
    repeated = runtime.initialize_core_strategy_account(
        database,
        initial_capital=50_000,
        initialized_at=NOW,
    )

    assert repeated == first
    with pytest.raises(ValueError, match="显式重置"):
        runtime.initialize_core_strategy_account(
            database,
            initial_capital=100_000,
            initialized_at=NOW,
        )


def test_automation_finalizes_without_manual_action_when_no_opinion_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-automatic.db"
    runtime.initialize_core_strategy_account(
        database,
        initial_capital=100_000,
        initialized_at=NOW,
    )
    monkeypatch.setattr(
        runtime,
        "read_capital_action_status",
        lambda *_, **__: SimpleNamespace(status="ready"),
    )
    monkeypatch.setattr(
        runtime,
        "prepare_strategy_investigations",
        lambda *_, **__: _preparation(),
    )
    monkeypatch.setattr(
        runtime,
        "schedule_automatic_collection_jobs",
        lambda *_, **__: [],
    )

    outcome = runtime.advance_core_strategy_automation(
        database,
        target_date=ACTUAL_DATE,
        now=NOW,
        force_prepare=True,
    )

    assert outcome.state == "finalized"
    assert outcome.status.stage == "finalized"
    assert outcome.status.portfolio_publication_complete is True
    assert runtime.next_core_strategy_automation_date(
        database,
        latest_due_date=date(2026, 7, 31),
    ) == date(2026, 7, 30)


def test_automation_starts_opinion_batch_and_waits_before_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-opinion.db"
    runtime.initialize_core_strategy_account(
        database,
        initial_capital=100_000,
        initialized_at=NOW,
    )
    monkeypatch.setattr(
        runtime,
        "read_capital_action_status",
        lambda *_, **__: SimpleNamespace(status="ready"),
    )

    def fake_prepare(
        *_: object,
        **__: object,
    ) -> StrategyInvestigationPreparation:
        publish_strategy_opinion_targets(
            database,
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            codes=["600001"],
            published_at=NOW,
        )
        return _preparation(["600001"])

    monkeypatch.setattr(
        runtime,
        "prepare_strategy_investigations",
        fake_prepare,
    )
    started: list[Path] = []

    outcome = runtime.advance_core_strategy_automation(
        database,
        target_date=ACTUAL_DATE,
        now=NOW,
        start_opinion_batch=started.append,
        force_prepare=True,
    )
    finalized_dates: list[date] = []

    def fake_finalize(
        *_: object,
        **kwargs: object,
    ) -> runtime.CoreStrategyRunStatus:
        actual_date = kwargs["actual_date"]
        assert isinstance(actual_date, date)
        finalized_dates.append(actual_date)
        return outcome.status.model_copy(
            update={
                "stage": "finalized",
                "message": "自动完成",
                "finalized_at": NOW + runtime.AUTOMATIC_FINALIZE_WAIT,
            }
        )

    monkeypatch.setattr(runtime, "finalize_core_strategy_day", fake_finalize)
    deadline_outcome = runtime.advance_core_strategy_automation(
        database,
        target_date=ACTUAL_DATE,
        now=NOW + runtime.AUTOMATIC_FINALIZE_WAIT,
    )

    assert outcome.state == "collecting_opinion"
    assert outcome.batch_start_requested is True
    assert outcome.status.auto_finalize_at == NOW + runtime.AUTOMATIC_FINALIZE_WAIT
    assert outcome.status.opinion_jobs.pending == 1
    assert started == [database]
    assert deadline_outcome.state == "finalized"
    assert finalized_dates == [ACTUAL_DATE]


def test_explicit_reset_clears_simulation_and_allows_reinitialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-reset.db"
    runtime.initialize_core_strategy_account(
        database,
        initial_capital=50_000,
        initialized_at=NOW,
    )
    monkeypatch.setattr(
        runtime,
        "read_capital_action_status",
        lambda *_, **__: SimpleNamespace(status="ready"),
    )
    monkeypatch.setattr(
        runtime,
        "prepare_strategy_investigations",
        lambda *_, **__: _preparation(),
    )
    monkeypatch.setattr(
        runtime,
        "schedule_automatic_collection_jobs",
        lambda *_, **__: [],
    )
    runtime.advance_core_strategy_automation(
        database,
        target_date=ACTUAL_DATE,
        now=NOW,
        force_prepare=True,
    )
    publish_strategy_opinion_targets(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        codes=["600001"],
        published_at=NOW,
    )
    schedule_opinion_jobs(
        database,
        actual_date=ACTUAL_DATE,
        now=NOW,
    )

    with pytest.raises(ValueError, match="清空全部"):
        runtime.reset_core_strategy_account(
            database,
            reset_at=NOW,
            confirmation="取消",
        )
    reset = runtime.reset_core_strategy_account(
        database,
        reset_at=NOW,
        confirmation="清空全部",
    )
    status = runtime.read_core_strategy_run_status(
        database,
        requested_date=ACTUAL_DATE,
    )
    reinitialized = runtime.initialize_core_strategy_account(
        database,
        initial_capital=80_000,
        initialized_at=NOW,
    )

    assert reset.previous_initial_capital == 50_000
    assert reset.removed_preparation_count == 1
    assert reset.removed_strategy_day_count == 1
    assert reset.removed_portfolio_count == 1
    assert reset.removed_opinion_target_count == 1
    assert reset.detached_opinion_job_count == 1
    assert status.stage == "account_uninitialized"
    assert reinitialized.initial_capital == 80_000
