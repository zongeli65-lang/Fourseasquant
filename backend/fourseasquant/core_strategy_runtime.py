from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from collections.abc import Callable
from typing import Literal, cast

from pydantic import BaseModel, Field

from fourseasquant.core_strategy import CORE_STRATEGY_VERSION
from fourseasquant.core_strategy_adapters import (
    StrategyInputUnavailable,
    read_strategy_opinion_evidence,
)
from fourseasquant.core_strategy_execution import PortfolioForEntry
from fourseasquant.core_strategy_orchestrator import (
    FinalizedStrategyDay,
    StrategyInvestigationPreparation,
    finalize_strategy_day,
    prepare_strategy_investigations,
)
from fourseasquant.core_strategy_positions import HoldingPosition
from fourseasquant.core_strategy_repository import (
    PublishedCoreStrategyDaySnapshot,
    PublishedCoreStrategyPortfolioSnapshot,
    create_core_strategy_tables,
    read_core_strategy_day,
    read_core_strategy_portfolio,
    read_latest_core_strategy_portfolio,
)
from fourseasquant.fundamental_queries import read_capital_action_status
from fourseasquant.public_opinion_repository import (
    JobStatus,
    create_public_opinion_tables,
    list_public_opinion_jobs,
    schedule_automatic_collection_jobs,
)
from fourseasquant.trading_calendar import is_trading_day


StrategyRuntimeStage = Literal[
    "account_uninitialized",
    "not_prepared",
    "blocked_inputs",
    "investigations_ready",
    "finalized",
    "failed",
]
StrategyRuntimeAction = Literal["prepare", "finalize"]
StrategyRuntimeAttemptStatus = Literal["succeeded", "blocked", "failed"]
StrategyAutomationState = Literal[
    "account_uninitialized",
    "waiting_inputs",
    "collecting_opinion",
    "finalized",
    "failed",
]
AUTOMATION_RETRY_INTERVAL = timedelta(minutes=5)
AUTOMATIC_FINALIZE_WAIT = timedelta(minutes=60)


class CoreStrategyAccount(BaseModel):
    initial_capital: float = Field(gt=0)
    initialized_at: datetime


class CoreStrategyOpinionJobCounts(BaseModel):
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0


class CoreStrategyRuntimeAttempt(BaseModel):
    requested_date: date
    actual_date: date | None
    strategy_version: str
    action: StrategyRuntimeAction
    status: StrategyRuntimeAttemptStatus
    attempted_at: datetime
    error_summary: str | None = None


class CoreStrategyRunStatus(BaseModel):
    requested_date: date
    actual_date: date | None
    strategy_version: str
    stage: StrategyRuntimeStage
    message: str
    account: CoreStrategyAccount | None
    candidate_count: int = 0
    fundamental_target_count: int = 0
    opinion_target_codes: list[str] = Field(default_factory=list)
    opinion_jobs: CoreStrategyOpinionJobCounts = Field(
        default_factory=CoreStrategyOpinionJobCounts
    )
    prepared_at: datetime | None = None
    auto_finalize_at: datetime | None = None
    finalized_at: datetime | None = None
    order_count: int = 0
    holding_count: int = 0
    available_cash: float | None = None
    net_asset_value: float | None = None
    portfolio_publication_complete: bool = False
    latest_attempt: CoreStrategyRuntimeAttempt | None = None


class CoreStrategyAutomationOutcome(BaseModel):
    target_date: date
    state: StrategyAutomationState
    reason: str
    batch_start_requested: bool = False
    status: CoreStrategyRunStatus


class CoreStrategyResetResult(BaseModel):
    reset_at: datetime
    previous_initial_capital: float | None
    removed_preparation_count: int
    removed_strategy_day_count: int
    removed_portfolio_count: int
    removed_fundamental_target_count: int
    removed_opinion_target_count: int
    detached_opinion_job_count: int


def create_core_strategy_runtime_tables(
    connection: sqlite3.Connection,
) -> None:
    create_core_strategy_tables(connection)
    create_public_opinion_tables(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_account (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            initial_capital REAL NOT NULL CHECK(initial_capital > 0),
            initialized_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_preparations (
            actual_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            requested_date TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            content_sha256 TEXT NOT NULL,
            prepared_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_core_strategy_preparations_requested
        ON core_strategy_preparations (
            requested_date DESC,
            prepared_at DESC,
            strategy_version DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_runtime_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requested_date TEXT NOT NULL,
            actual_date TEXT,
            strategy_version TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('prepare', 'finalize')),
            status TEXT NOT NULL
                CHECK(status IN ('succeeded', 'blocked', 'failed')),
            attempted_at TEXT NOT NULL,
            error_summary TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_core_strategy_runtime_attempts_latest
        ON core_strategy_runtime_attempts (
            requested_date DESC,
            id DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_reset_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reset_at TEXT NOT NULL,
            previous_initial_capital REAL,
            removed_preparation_count INTEGER NOT NULL,
            removed_strategy_day_count INTEGER NOT NULL,
            removed_portfolio_count INTEGER NOT NULL,
            removed_fundamental_target_count INTEGER NOT NULL,
            removed_opinion_target_count INTEGER NOT NULL,
            detached_opinion_job_count INTEGER NOT NULL
        )
        """
    )


def initialize_core_strategy_account(
    path: Path,
    *,
    initial_capital: float,
    initialized_at: datetime,
) -> CoreStrategyAccount:
    """一次性初始化模拟账户；已经初始化后不允许静默改变资金语义。"""

    account = CoreStrategyAccount(
        initial_capital=initial_capital,
        initialized_at=initialized_at,
    )
    if initialized_at.tzinfo is None:
        raise ValueError("账户初始化时间必须包含时区")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        existing = connection.execute(
            """
            SELECT initial_capital, initialized_at
            FROM core_strategy_account
            WHERE singleton_id = 1
            """
        ).fetchone()
        if existing is not None:
            stored = CoreStrategyAccount(
                initial_capital=float(existing[0]),
                initialized_at=datetime.fromisoformat(cast(str, existing[1])),
            )
            if abs(stored.initial_capital - account.initial_capital) > 0.01:
                raise ValueError(
                    "核心策略账户已经初始化；修改初始资金需要显式重置模拟账户"
                )
            return stored
        connection.execute(
            """
            INSERT INTO core_strategy_account (
                singleton_id,
                initial_capital,
                initialized_at
            ) VALUES (1, ?, ?)
            """,
            (account.initial_capital, account.initialized_at.isoformat()),
        )
    return account


def reset_core_strategy_account(
    path: Path,
    *,
    reset_at: datetime,
    confirmation: str,
) -> CoreStrategyResetResult:
    """
    显式清空整个模拟账户生命周期，同时保留独立的重置审计事件。

    舆论内容证据不属于模拟账户资产，不会删除；原策略任务会脱离当前
    策略版本，确保重新初始化后同日任务可以重新生成。
    """

    if confirmation != "清空全部":
        raise ValueError("必须明确确认“清空全部”")
    if reset_at.tzinfo is None:
        raise ValueError("账户重置时间必须包含时区")
    _ensure_runtime_database(path)
    detached_version = f"reset:{reset_at.isoformat()}"
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        account_row = connection.execute(
            """
            SELECT initial_capital
            FROM core_strategy_account
            WHERE singleton_id = 1
            """
        ).fetchone()
        previous_initial_capital = (
            float(account_row[0]) if account_row is not None else None
        )
        counts = {
            "preparations": _table_count(
                connection, "core_strategy_preparations"
            ),
            "days": _table_count(
                connection, "core_strategy_day_snapshots"
            ),
            "portfolios": _table_count(
                connection, "core_strategy_portfolio_snapshots"
            ),
            "fundamental_targets": _table_count(
                connection, "strategy_fundamental_target_snapshots"
            ),
            "opinion_targets": _table_count(
                connection, "strategy_opinion_target_snapshots"
            ),
        }
        detached_jobs = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM public_opinion_collection_jobs
                WHERE target_source = 'strategy'
                """
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE public_opinion_collection_jobs
            SET target_version = ?,
                status = CASE
                    WHEN status IN ('pending', 'running', 'failed')
                    THEN 'blocked'
                    ELSE status
                END,
                updated_at = ?,
                error_summary = CASE
                    WHEN status IN ('pending', 'running', 'failed')
                    THEN '模拟账户已显式重置，任务不再属于当前策略'
                    ELSE error_summary
                END
            WHERE target_source = 'strategy'
            """,
            (detached_version, reset_at.isoformat()),
        )
        for table in (
            "core_strategy_portfolio_snapshots",
            "core_strategy_day_snapshots",
            "core_strategy_preparations",
            "core_strategy_runtime_attempts",
            "strategy_fundamental_target_snapshots",
            "strategy_opinion_target_snapshots",
            "core_strategy_account",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            """
            INSERT INTO core_strategy_reset_events (
                reset_at,
                previous_initial_capital,
                removed_preparation_count,
                removed_strategy_day_count,
                removed_portfolio_count,
                removed_fundamental_target_count,
                removed_opinion_target_count,
                detached_opinion_job_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reset_at.isoformat(),
                previous_initial_capital,
                counts["preparations"],
                counts["days"],
                counts["portfolios"],
                counts["fundamental_targets"],
                counts["opinion_targets"],
                detached_jobs,
            ),
        )
    return CoreStrategyResetResult(
        reset_at=reset_at,
        previous_initial_capital=previous_initial_capital,
        removed_preparation_count=counts["preparations"],
        removed_strategy_day_count=counts["days"],
        removed_portfolio_count=counts["portfolios"],
        removed_fundamental_target_count=counts["fundamental_targets"],
        removed_opinion_target_count=counts["opinion_targets"],
        detached_opinion_job_count=detached_jobs,
    )


def advance_core_strategy_automation(
    path: Path,
    *,
    target_date: date,
    now: datetime,
    start_opinion_batch: Callable[[Path], object] | None = None,
    force_prepare: bool = False,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> CoreStrategyAutomationOutcome:
    """
    将一个策略日自动推进到当前输入允许的最远阶段。

    调用方只需周期性调用本接口。模块内部负责幂等准备、舆论批次启动、
    失败重试节流，以及调查终态或等待上限后的自动完成。
    """

    if now.tzinfo is None:
        raise ValueError("策略自动推进时间必须包含时区")
    status = read_core_strategy_run_status(
        path,
        requested_date=target_date,
        strategy_version=strategy_version,
    )
    if status.account is None:
        return CoreStrategyAutomationOutcome(
            target_date=target_date,
            state="account_uninitialized",
            reason="模拟账户尚未初始化，自动策略保持停用",
            status=status,
        )
    if target_date < status.account.initialized_at.date():
        return CoreStrategyAutomationOutcome(
            target_date=target_date,
            state="waiting_inputs",
            reason="目标交易日早于本次模拟账户初始化日期",
            status=status,
        )
    if status.stage in {"not_prepared", "blocked_inputs", "failed"}:
        retry_due = (
            status.latest_attempt is None
            or now - status.latest_attempt.attempted_at
            >= AUTOMATION_RETRY_INTERVAL
        )
        if force_prepare or retry_due:
            try:
                status = prepare_core_strategy_day(
                    path,
                    requested_date=target_date,
                    prepared_at=now,
                    strategy_version=strategy_version,
                )
            except Exception as error:
                status = read_core_strategy_run_status(
                    path,
                    requested_date=target_date,
                    strategy_version=strategy_version,
                )
                return CoreStrategyAutomationOutcome(
                    target_date=target_date,
                    state="failed",
                    reason=_safe_error_summary(error),
                    status=status,
                )
        if status.stage != "investigations_ready":
            return CoreStrategyAutomationOutcome(
                target_date=target_date,
                state="waiting_inputs",
                reason=status.message,
                status=status,
            )
    if status.stage == "finalized":
        return CoreStrategyAutomationOutcome(
            target_date=target_date,
            state="finalized",
            reason=status.message,
            status=status,
        )
    if status.stage != "investigations_ready":
        return CoreStrategyAutomationOutcome(
            target_date=target_date,
            state="failed",
            reason=status.message,
            status=status,
        )

    counts = status.opinion_jobs
    total_jobs = (
        counts.pending
        + counts.running
        + counts.succeeded
        + counts.failed
        + counts.blocked
    )
    terminal_without_retry = (
        counts.pending == 0
        and counts.running == 0
        and counts.failed == 0
    )
    deadline = status.auto_finalize_at
    should_finalize = (
        total_jobs == 0
        or terminal_without_retry
        or (deadline is not None and now >= deadline)
    )
    if should_finalize:
        try:
            finalized = finalize_core_strategy_day(
                path,
                actual_date=status.actual_date or target_date,
                finalized_at=now,
                strategy_version=strategy_version,
            )
        except Exception as error:
            failed_status = read_core_strategy_run_status(
                path,
                requested_date=target_date,
                strategy_version=strategy_version,
            )
            return CoreStrategyAutomationOutcome(
                target_date=target_date,
                state="failed",
                reason=_safe_error_summary(error),
                status=failed_status,
            )
        reason = (
            "舆论调查达到终态，策略日已自动完成"
            if deadline is None or now < deadline
            else "舆论等待达到上限，策略已按当前完整证据自动完成"
        )
        return CoreStrategyAutomationOutcome(
            target_date=target_date,
            state="finalized",
            reason=reason,
            status=finalized,
        )

    batch_start_requested = False
    batch_error: str | None = None
    if start_opinion_batch is not None:
        try:
            start_opinion_batch(path)
            batch_start_requested = True
        except Exception as error:
            batch_error = _safe_error_summary(error)
    return CoreStrategyAutomationOutcome(
        target_date=target_date,
        state="collecting_opinion",
        reason=(
            f"舆论后台批次启动失败，将在等待上限后继续：{batch_error}"
            if batch_error is not None
            else "舆论调查正在后台自动执行，完成后将自动管理仓位"
        ),
        batch_start_requested=batch_start_requested,
        status=status,
    )


def next_core_strategy_automation_date(
    path: Path,
    *,
    latest_due_date: date,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> date | None:
    """返回本次账户生命周期内最早尚未完成的应处理交易日。"""

    if not path.exists():
        return None
    _ensure_runtime_database(path)
    account = _read_account(path)
    if account is None:
        return None
    candidate = account.initialized_at.date()
    while candidate <= latest_due_date:
        if (
            is_trading_day(candidate)
            and read_core_strategy_day(
                path,
                actual_date=candidate,
                strategy_version=strategy_version,
            )
            is None
        ):
            return candidate
        candidate += timedelta(days=1)
    return None


def prepare_core_strategy_day(
    path: Path,
    *,
    requested_date: date,
    prepared_at: datetime,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> CoreStrategyRunStatus:
    """在收盘数据发布后生成并持久化两阶段调查准备结果。"""

    if prepared_at.tzinfo is None:
        raise ValueError("策略准备时间必须包含时区")
    _ensure_runtime_database(path)
    account = _read_account(path)
    if account is None:
        return read_core_strategy_run_status(
            path,
            requested_date=requested_date,
            strategy_version=strategy_version,
        )
    published = read_core_strategy_day(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    if published is not None:
        return read_core_strategy_run_status(
            path,
            requested_date=requested_date,
            strategy_version=strategy_version,
        )
    existing = _read_preparation(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    if existing is not None:
        schedule_automatic_collection_jobs(
            path,
            actual_date=existing.daily_inputs.actual_date,
            now=prepared_at,
        )
        return read_core_strategy_run_status(
            path,
            requested_date=existing.daily_inputs.actual_date,
            strategy_version=strategy_version,
        )

    capital_actions = read_capital_action_status(
        path,
        target_date=requested_date,
    )
    if capital_actions.status != "ready":
        message = (
            "公司行为数据尚未形成同日完整发布，核心策略准备已暂停"
        )
        _record_attempt(
            path,
            requested_date=requested_date,
            actual_date=None,
            strategy_version=strategy_version,
            action="prepare",
            status="blocked",
            attempted_at=prepared_at,
            error_summary=message,
        )
        return read_core_strategy_run_status(
            path,
            requested_date=requested_date,
            strategy_version=strategy_version,
        )

    portfolio, _ = _starting_portfolio(
        path,
        account=account,
        before_date=requested_date,
    )
    try:
        preparation = prepare_strategy_investigations(
            path,
            requested_date=requested_date,
            strategy_version=strategy_version,
            corporate_actions_complete=True,
            portfolio=portfolio,
            fundamental_investigations=None,
            published_at=prepared_at,
        )
        _publish_preparation(
            path,
            preparation=preparation,
            prepared_at=prepared_at,
        )
        schedule_automatic_collection_jobs(
            path,
            actual_date=preparation.daily_inputs.actual_date,
            now=prepared_at,
        )
        _record_attempt(
            path,
            requested_date=requested_date,
            actual_date=preparation.daily_inputs.actual_date,
            strategy_version=strategy_version,
            action="prepare",
            status="succeeded",
            attempted_at=prepared_at,
            error_summary=None,
        )
    except StrategyInputUnavailable as error:
        _record_attempt(
            path,
            requested_date=requested_date,
            actual_date=None,
            strategy_version=strategy_version,
            action="prepare",
            status="blocked",
            attempted_at=prepared_at,
            error_summary=str(error),
        )
        return read_core_strategy_run_status(
            path,
            requested_date=requested_date,
            strategy_version=strategy_version,
        )
    except Exception as error:
        _record_attempt(
            path,
            requested_date=requested_date,
            actual_date=None,
            strategy_version=strategy_version,
            action="prepare",
            status="failed",
            attempted_at=prepared_at,
            error_summary=_safe_error_summary(error),
        )
        raise
    return read_core_strategy_run_status(
        path,
        requested_date=preparation.daily_inputs.actual_date,
        strategy_version=strategy_version,
    )


def finalize_core_strategy_day(
    path: Path,
    *,
    actual_date: date,
    finalized_at: datetime,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> CoreStrategyRunStatus:
    """读取当前调查证据，完成模拟订单并发布完整策略日。"""

    if finalized_at.tzinfo is None:
        raise ValueError("策略完成时间必须包含时区")
    _ensure_runtime_database(path)
    account = _read_account(path)
    if account is None:
        raise LookupError("核心策略账户尚未初始化")
    existing_day = read_core_strategy_day(
        path,
        actual_date=actual_date,
        strategy_version=strategy_version,
    )
    if existing_day is not None:
        return read_core_strategy_run_status(
            path,
            requested_date=actual_date,
            strategy_version=strategy_version,
        )
    preparation = _read_preparation(
        path,
        actual_date=actual_date,
        strategy_version=strategy_version,
    )
    if preparation is None:
        raise LookupError("尚未生成该交易日的策略调查准备结果")
    portfolio, positions = _starting_portfolio(
        path,
        account=account,
        before_date=actual_date,
    )
    candidate_codes = [
        candidate.preliminary.code
        for candidate in preparation.daily_inputs.candidates
    ]
    opinions = read_strategy_opinion_evidence(
        path,
        actual_date=actual_date,
        candidate_codes=candidate_codes,
        targeted_codes=preparation.opinion_targets.codes,
    )
    try:
        result: FinalizedStrategyDay = finalize_strategy_day(
            path,
            preparation=preparation,
            opinions=opinions,
            portfolio=portfolio,
            positions=positions,
            published_at=finalized_at,
        )
        _record_attempt(
            path,
            requested_date=actual_date,
            actual_date=actual_date,
            strategy_version=strategy_version,
            action="finalize",
            status="succeeded",
            attempted_at=finalized_at,
            error_summary=(
                None
                if result.portfolio_publication_status == "published"
                else "持仓行情不完整，组合快照未发布"
            ),
        )
    except Exception as error:
        _record_attempt(
            path,
            requested_date=actual_date,
            actual_date=actual_date,
            strategy_version=strategy_version,
            action="finalize",
            status="failed",
            attempted_at=finalized_at,
            error_summary=_safe_error_summary(error),
        )
        raise
    return read_core_strategy_run_status(
        path,
        requested_date=actual_date,
        strategy_version=strategy_version,
    )


def read_core_strategy_run_status(
    path: Path,
    *,
    requested_date: date,
    strategy_version: str = CORE_STRATEGY_VERSION,
) -> CoreStrategyRunStatus:
    """读取指定交易日的两阶段运行状态，不触发任何外部采集。"""

    if not path.exists():
        return CoreStrategyRunStatus(
            requested_date=requested_date,
            actual_date=None,
            strategy_version=strategy_version,
            stage="account_uninitialized",
            message="请先设置模拟账户初始资金",
            account=None,
        )
    _ensure_runtime_database(path)
    account = _read_account(path)
    if account is None:
        return CoreStrategyRunStatus(
            requested_date=requested_date,
            actual_date=None,
            strategy_version=strategy_version,
            stage="account_uninitialized",
            message="请先设置模拟账户初始资金",
            account=None,
            latest_attempt=_read_latest_attempt(
                path,
                requested_date=requested_date,
                strategy_version=strategy_version,
            ),
        )
    day = read_core_strategy_day(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    portfolio = read_core_strategy_portfolio(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    preparation = _read_preparation(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    latest_attempt = _read_latest_attempt(
        path,
        requested_date=requested_date,
        strategy_version=strategy_version,
    )
    opinion_codes = (
        list(preparation.opinion_targets.codes)
        if preparation is not None
        else []
    )
    candidate_count = (
        len(day.snapshot.candidate_pipeline.candidates)
        if day is not None
        else (
            len(preparation.candidate_pipeline.candidates)
            if preparation is not None
            else 0
        )
    )
    fundamental_target_count = (
        len(preparation.fundamental_targets.targets)
        if preparation is not None
        else 0
    )
    job_counts = _opinion_job_counts(
        path,
        actual_date=requested_date,
        strategy_version=strategy_version,
    )
    if day is not None:
        return _finalized_status(
            requested_date=requested_date,
            account=account,
            day=day,
            portfolio=portfolio,
            preparation=preparation,
            candidate_count=candidate_count,
            fundamental_target_count=fundamental_target_count,
            opinion_codes=opinion_codes,
            job_counts=job_counts,
            latest_attempt=latest_attempt,
        )
    if preparation is not None:
        auto_finalize_at = (
            preparation.opinion_targets.published_at
            + AUTOMATIC_FINALIZE_WAIT
        )
        return CoreStrategyRunStatus(
            requested_date=requested_date,
            actual_date=preparation.daily_inputs.actual_date,
            strategy_version=strategy_version,
            stage="investigations_ready",
            message="调查目标已经发布，系统正在自动完成舆论与模拟交易",
            account=account,
            candidate_count=candidate_count,
            fundamental_target_count=fundamental_target_count,
            opinion_target_codes=opinion_codes,
            opinion_jobs=job_counts,
            prepared_at=preparation.opinion_targets.published_at,
            auto_finalize_at=auto_finalize_at,
            latest_attempt=latest_attempt,
        )
    if latest_attempt is not None and latest_attempt.status != "succeeded":
        return CoreStrategyRunStatus(
            requested_date=requested_date,
            actual_date=latest_attempt.actual_date,
            strategy_version=strategy_version,
            stage=(
                "blocked_inputs"
                if latest_attempt.status == "blocked"
                else "failed"
            ),
            message=latest_attempt.error_summary or "核心策略运行失败",
            account=account,
            latest_attempt=latest_attempt,
        )
    return CoreStrategyRunStatus(
        requested_date=requested_date,
        actual_date=None,
        strategy_version=strategy_version,
        stage="not_prepared",
        message="该交易日尚未生成核心策略调查目标",
        account=account,
        latest_attempt=latest_attempt,
    )


def _ensure_runtime_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)


def _read_account(path: Path) -> CoreStrategyAccount | None:
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        row = connection.execute(
            """
            SELECT initial_capital, initialized_at
            FROM core_strategy_account
            WHERE singleton_id = 1
            """
        ).fetchone()
    if row is None:
        return None
    return CoreStrategyAccount(
        initial_capital=float(row[0]),
        initialized_at=datetime.fromisoformat(cast(str, row[1])),
    )


def _starting_portfolio(
    path: Path,
    *,
    account: CoreStrategyAccount,
    before_date: date,
) -> tuple[PortfolioForEntry, list[HoldingPosition]]:
    previous = read_latest_core_strategy_portfolio(
        path,
        as_of_date=before_date - timedelta(days=1),
    )
    if previous is None:
        return (
            PortfolioForEntry(
                initial_capital=account.initial_capital,
                net_asset_value=account.initial_capital,
                available_cash=account.initial_capital,
                held_codes=[],
            ),
            [],
        )
    snapshot = previous.snapshot
    if abs(snapshot.initial_capital - account.initial_capital) > 0.01:
        raise ValueError("组合快照与账户初始资金不一致")
    return (
        PortfolioForEntry(
            initial_capital=snapshot.initial_capital,
            net_asset_value=snapshot.net_asset_value,
            available_cash=snapshot.available_cash,
            held_codes=[
                position.code for position in snapshot.positions
            ],
        ),
        [position.model_copy(deep=True) for position in snapshot.positions],
    )


def _publish_preparation(
    path: Path,
    *,
    preparation: StrategyInvestigationPreparation,
    prepared_at: datetime,
) -> None:
    payload_json = preparation.model_dump_json()
    content_sha256 = hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()
    actual_date = preparation.daily_inputs.actual_date
    strategy_version = preparation.opinion_targets.strategy_version
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        existing = connection.execute(
            """
            SELECT content_sha256
            FROM core_strategy_preparations
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (actual_date.isoformat(), strategy_version),
        ).fetchone()
        if existing is not None:
            if cast(str, existing[0]) != content_sha256:
                raise ValueError(
                    "同日同策略版本的调查准备结果已发布；"
                    "修改内容必须提升策略版本"
                )
            return
        connection.execute(
            """
            INSERT INTO core_strategy_preparations (
                actual_date,
                strategy_version,
                requested_date,
                payload_json,
                content_sha256,
                prepared_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actual_date.isoformat(),
                strategy_version,
                preparation.daily_inputs.requested_date.isoformat(),
                payload_json,
                content_sha256,
                prepared_at.isoformat(),
            ),
        )


def _read_preparation(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str,
) -> StrategyInvestigationPreparation | None:
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        row = connection.execute(
            """
            SELECT payload_json
            FROM core_strategy_preparations
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (actual_date.isoformat(), strategy_version),
        ).fetchone()
    if row is None:
        return None
    return StrategyInvestigationPreparation.model_validate_json(
        cast(str, row[0])
    )


def _record_attempt(
    path: Path,
    *,
    requested_date: date,
    actual_date: date | None,
    strategy_version: str,
    action: StrategyRuntimeAction,
    status: StrategyRuntimeAttemptStatus,
    attempted_at: datetime,
    error_summary: str | None,
) -> None:
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        connection.execute(
            """
            INSERT INTO core_strategy_runtime_attempts (
                requested_date,
                actual_date,
                strategy_version,
                action,
                status,
                attempted_at,
                error_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                requested_date.isoformat(),
                actual_date.isoformat() if actual_date is not None else None,
                strategy_version,
                action,
                status,
                attempted_at.isoformat(),
                error_summary,
            ),
        )


def _read_latest_attempt(
    path: Path,
    *,
    requested_date: date,
    strategy_version: str,
) -> CoreStrategyRuntimeAttempt | None:
    with sqlite3.connect(path) as connection:
        create_core_strategy_runtime_tables(connection)
        row = connection.execute(
            """
            SELECT requested_date, actual_date, strategy_version, action,
                   status, attempted_at, error_summary
            FROM core_strategy_runtime_attempts
            WHERE requested_date = ? AND strategy_version = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (requested_date.isoformat(), strategy_version),
        ).fetchone()
    if row is None:
        return None
    return CoreStrategyRuntimeAttempt(
        requested_date=date.fromisoformat(cast(str, row[0])),
        actual_date=(
            date.fromisoformat(cast(str, row[1]))
            if row[1] is not None
            else None
        ),
        strategy_version=cast(str, row[2]),
        action=cast(StrategyRuntimeAction, row[3]),
        status=cast(StrategyRuntimeAttemptStatus, row[4]),
        attempted_at=datetime.fromisoformat(cast(str, row[5])),
        error_summary=cast(str | None, row[6]),
    )


def _opinion_job_counts(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str,
) -> CoreStrategyOpinionJobCounts:
    counts: dict[JobStatus, int] = {
        "pending": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "blocked": 0,
    }
    for job in list_public_opinion_jobs(path):
        if (
            job.target_source == "strategy"
            and job.target_version == strategy_version
            and job.end_date == actual_date
        ):
            counts[job.status] += 1
    return CoreStrategyOpinionJobCounts(**counts)


def _finalized_status(
    *,
    requested_date: date,
    account: CoreStrategyAccount,
    day: PublishedCoreStrategyDaySnapshot,
    portfolio: PublishedCoreStrategyPortfolioSnapshot | None,
    preparation: StrategyInvestigationPreparation | None,
    candidate_count: int,
    fundamental_target_count: int,
    opinion_codes: list[str],
    job_counts: CoreStrategyOpinionJobCounts,
    latest_attempt: CoreStrategyRuntimeAttempt | None,
) -> CoreStrategyRunStatus:
    snapshot = day.snapshot
    holding_count = (
        len(snapshot.position_management.positions)
        + len(snapshot.entry_planning.orders)
    )
    return CoreStrategyRunStatus(
        requested_date=requested_date,
        actual_date=snapshot.actual_date,
        strategy_version=snapshot.strategy_version,
        stage="finalized",
        message=(
            "核心策略日和完整组合已经发布"
            if portfolio is not None
            else "核心策略日已发布，但组合因持仓行情不完整而保留上一版"
        ),
        account=account,
        candidate_count=candidate_count,
        fundamental_target_count=fundamental_target_count,
        opinion_target_codes=opinion_codes,
        opinion_jobs=job_counts,
        prepared_at=(
            preparation.opinion_targets.published_at
            if preparation is not None
            else None
        ),
        auto_finalize_at=(
            preparation.opinion_targets.published_at
            + AUTOMATIC_FINALIZE_WAIT
            if preparation is not None
            else None
        ),
        finalized_at=day.published_at,
        order_count=(
            len(snapshot.position_management.orders)
            + len(snapshot.entry_planning.orders)
        ),
        holding_count=holding_count,
        available_cash=(
            portfolio.snapshot.available_cash
            if portfolio is not None
            else snapshot.entry_planning.remaining_cash
        ),
        net_asset_value=(
            portfolio.snapshot.net_asset_value
            if portfolio is not None
            else None
        ),
        portfolio_publication_complete=portfolio is not None,
        latest_attempt=latest_attempt,
    )


def _safe_error_summary(error: Exception) -> str:
    message = " ".join(str(error).split())
    message = re.sub(
        r"(?i)(api[_-]?key|token|password|authorization)(\s*[=:]\s*)\S+",
        r"\1\2[REDACTED]",
        message,
    )
    detail = message[:300] if message else "未提供异常详情"
    return f"{type(error).__name__}: {detail}"


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
