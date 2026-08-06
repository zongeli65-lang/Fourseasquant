from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from fourseasquant._market_regime_v8_structure import MarketBar
from fourseasquant.market_regime_v8 import (
    INDEX_NAMES as V8_INDEX_NAMES,
    calculate_market_regime_v8,
)
from fourseasquant.sqlite_connection import open_database_connection

BEIJING = ZoneInfo("Asia/Shanghai")
HISTORY_SOURCE = "akshare_sina_daily"
RULES_VERSION = "market-environment-contextual-momentum-v8"
FAST_RETURN_THRESHOLD = 0.50
FAST_SLOPE_THRESHOLD = 0.08
SWING_LEFT = 3
SWING_RIGHT = 3
MIN_HISTORY_DAYS = 20

TrendState = Literal["rising", "falling", "sideways"]
Direction = Literal["bullish", "bearish", "neutral"]
QualityState = Literal["strong", "neutral", "weak"]
CapacityState = Literal["abundant", "normal", "insufficient"]
ValidationState = Literal["pending", "validated", "not_required"]
WarningDirection = Literal["risk", "support"]
MomentumPhase = Literal[
    "bullish_impulse",
    "bullish_exhaustion",
    "bearish_impulse",
    "bearish_exhaustion",
    "balance",
]
MomentumEvent = Literal[
    "bullish_shock",
    "bearish_shock",
    "top_exhaustion",
    "bottom_exhaustion",
    "bullish_impulse",
    "bearish_impulse",
    "balance",
    "none",
]


def index_source(symbol: str) -> str:
    return f"akshare_index_{symbol}"


class IndexTrendEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    close: float
    change_pct: float
    fast_direction: Direction
    fast_bull_votes: int = Field(ge=0, le=3)
    fast_bear_votes: int = Field(ge=0, le=3)
    return_3d_standardized: float | None
    slope_5d_standardized: float | None
    breakout_10d: bool
    breakdown_10d: bool
    slow_bull_votes: int = Field(ge=0, le=3)
    slow_bear_votes: int = Field(ge=0, le=3)
    return_10d_standardized: float | None
    slope_10d_standardized: float | None
    swing_structure: Direction
    breakout_held_2d: bool
    breakdown_held_2d: bool


class MarketBreadthEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: QualityState
    eligible_count: int
    advancers: int
    decliners: int
    unchanged: int
    advancer_ratio: float
    advancer_ratio_5d_average: float
    positive_breadth_days_5d: int
    new_high_20d: int
    new_low_20d: int
    high_low_ratio: float
    strong_votes: int = Field(ge=0, le=3)
    weak_votes: int = Field(ge=0, le=3)


class MarketCapacityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CapacityState
    turnover_cny: int
    turnover_20d_median_cny: int
    ratio_to_20d_median: float


class CostPressureProxy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_days: int
    upper_trapped_pressure_pct: float
    lower_profit_pressure_pct: float
    display_only: bool = True
    explanation: str


class MarketEnvironmentWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warning_id: str
    warning_type: str
    direction: WarningDirection
    index_code: str
    index_name: str
    trigger_date: date
    description: str
    invalidation_level: float


class ContextualMomentumEvidence(BaseModel):
    """第八版正式判定证据；旧快速、广度和形态字段仅作辅助展示。"""

    model_config = ConfigDict(extra="forbid")

    baseline_state: TrendState
    momentum_phase: MomentumPhase
    momentum_event: MomentumEvent
    prior_directional_streak: int = Field(ge=0)
    prior_move_atr: float
    prior_directional_event_count: int = Field(ge=0)
    overextended_context: Literal["bullish", "bearish"] | None
    reversal_candidate: Literal["bullish", "bearish"] | None
    candidate_age: int = Field(ge=0)
    strong_reversal_verified: bool
    contextual_takeover: bool
    active_override: Literal["bullish", "bearish"] | None
    released_to_sideways: bool
    close_impulse_atr: float
    body_impulse_atr: float
    close_location: float
    advancing_index_count: int = Field(ge=0, le=5)
    declining_index_count: int = Field(ge=0, le=5)
    bullish_one_atr_count: int = Field(ge=0, le=5)
    bearish_one_atr_count: int = Field(ge=0, le=5)


class MarketEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["market-environment-snapshot-v2"]
    actual_data_date: date
    rules_version: str
    trend_id: str
    trend_state: TrendState
    trend_changed: bool
    trend_start_date: date
    validation_state: ValidationState
    validation_deadline: date | None
    fast_bull_streak: int = Field(ge=0)
    fast_bear_streak: int = Field(ge=0)
    sideways_streak: int = Field(ge=0)
    extreme_decline: bool
    contextual_momentum: ContextualMomentumEvidence
    indices: list[IndexTrendEvidence] = Field(min_length=2, max_length=2)
    breadth: MarketBreadthEvidence
    capacity: MarketCapacityEvidence
    cost_pressure: CostPressureProxy
    warnings: list[MarketEnvironmentWarning]
    reasons: list[str]
    data_sources: list[str]
    generated_at: datetime


class MarketEnvironmentHistory(BaseModel):
    requested_end_date: date
    rules_version: str
    items: list[MarketEnvironmentSnapshot]


class MarketEnvironmentRefreshResult(BaseModel):
    run_id: str
    requested_date: date
    actual_data_date: date
    rules_version: str
    inserted_count: int
    snapshot_count: int
    completed_at: datetime


class MarketEnvironmentUnavailable(LookupError):
    pass


@dataclass(frozen=True)
class _IndexBar:
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class _BreadthAccumulator:
    advancers: int = 0
    decliners: int = 0
    unchanged: int = 0
    turnover_cny: int = 0
    eligible_count: int = 0
    limit_up: int = 0
    limit_down: int = 0
    new_high_20d: int = 0
    new_low_20d: int = 0


@dataclass(frozen=True)
class _BreadthDay:
    trading_date: date
    advancers: int
    decliners: int
    unchanged: int
    turnover_cny: int
    eligible_count: int
    limit_up: int
    limit_down: int
    new_high_20d: int
    new_low_20d: int


@dataclass(frozen=True)
class _WarningTrigger:
    warning_type: str
    direction: WarningDirection
    index_code: str
    index_name: str
    trigger_index: int
    trigger_date: date
    description: str
    invalidation_level: float
    maximum_age: int


@dataclass(frozen=True)
class _IndexDay:
    evidence: IndexTrendEvidence
    mr20: float
    warnings: tuple[_WarningTrigger, ...]


def create_market_environment_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS market_environment_snapshots (
            actual_data_date TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            trend_state TEXT NOT NULL CHECK (
                trend_state IN ('rising', 'falling', 'sideways')
            ),
            trend_id TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL UNIQUE,
            snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
            generated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (actual_data_date, rules_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_environment_latest
        ON market_environment_snapshots (
            rules_version, actual_data_date DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS market_environment_runs (
            run_id TEXT PRIMARY KEY,
            requested_date TEXT NOT NULL,
            actual_data_date TEXT,
            rules_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
            inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0),
            snapshot_count INTEGER NOT NULL CHECK (snapshot_count >= 0),
            error_summary TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL
        )
        """
    )
    for table in ("market_environment_snapshots", "market_environment_runs"):
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_reject_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '市场环境历史记录不可修改');
            END
            """
        )
        connection.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_reject_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '市场环境历史记录不可删除');
            END
            """
        )


def refresh_market_environment(
    path: Path,
    *,
    requested_date: date,
    now: datetime | None = None,
) -> MarketEnvironmentRefreshResult:
    started_at = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    run_id = f"market-env-run-{uuid.uuid4().hex}"
    try:
        snapshots = replay_market_environment(path, requested_date=requested_date)
        if not snapshots:
            raise MarketEnvironmentUnavailable("市场环境数据不足20个共同交易日")
        completed_at = datetime.now(BEIJING)
        inserted_count = _publish_snapshots(
            path,
            snapshots=snapshots,
            run_id=run_id,
            requested_date=requested_date,
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception as error:
        completed_at = datetime.now(BEIJING)
        with open_database_connection(path) as connection:
            connection.execute(
                """
                INSERT INTO market_environment_runs (
                    run_id, requested_date, actual_data_date, rules_version,
                    status, inserted_count, snapshot_count, error_summary,
                    started_at, completed_at
                ) VALUES (?, ?, NULL, ?, 'failed', 0, 0, ?, ?, ?)
                """,
                (
                    run_id,
                    requested_date.isoformat(),
                    RULES_VERSION,
                    f"{type(error).__name__}: {error}",
                    started_at.isoformat(),
                    completed_at.isoformat(),
                ),
            )
        raise
    return MarketEnvironmentRefreshResult(
        run_id=run_id,
        requested_date=requested_date,
        actual_data_date=snapshots[-1].actual_data_date,
        rules_version=RULES_VERSION,
        inserted_count=inserted_count,
        snapshot_count=len(snapshots),
        completed_at=completed_at,
    )


def read_market_environment(
    path: Path,
    *,
    requested_date: date,
) -> MarketEnvironmentSnapshot:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT snapshot_json
            FROM market_environment_snapshots
            WHERE rules_version = ? AND actual_data_date <= ?
            ORDER BY actual_data_date DESC
            LIMIT 1
            """,
            (RULES_VERSION, requested_date.isoformat()),
        ).fetchone()
    if row is None:
        raise MarketEnvironmentUnavailable("目标日期前尚无市场环境快照")
    return MarketEnvironmentSnapshot.model_validate_json(cast(str, row[0]))


def read_market_environment_history(
    path: Path,
    *,
    requested_end_date: date,
    limit: int = 60,
) -> MarketEnvironmentHistory:
    if not 1 <= limit <= 500:
        raise ValueError("limit 必须在1到500之间")
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT snapshot_json
            FROM market_environment_snapshots
            WHERE rules_version = ? AND actual_data_date <= ?
            ORDER BY actual_data_date DESC
            LIMIT ?
            """,
            (RULES_VERSION, requested_end_date.isoformat(), limit),
        ).fetchall()
    items = [
        MarketEnvironmentSnapshot.model_validate_json(cast(str, row[0]))
        for row in reversed(rows)
    ]
    return MarketEnvironmentHistory(
        requested_end_date=requested_end_date,
        rules_version=RULES_VERSION,
        items=items,
    )


def replay_market_environment(
    path: Path,
    *,
    requested_date: date,
    generated_at: datetime | None = None,
) -> list[MarketEnvironmentSnapshot]:
    index_bars = _load_index_bars(path, requested_date)
    breadth_by_date = _load_main_board_breadth(path, requested_date)
    index_days = {
        code: _calculate_index_days(code, name, bars)
        for code, (name, bars) in index_bars.items()
    }
    v8_days = calculate_market_regime_v8(
        {
            code: [
                MarketBar(
                    trading_date=bar.trading_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
                for bar in bars
            ]
            for code, (_, bars) in index_bars.items()
        }
    )
    v8_by_date = {item.trading_date: item for item in v8_days}
    common_dates = sorted(
        set(index_days["sh000001"])
        & set(index_days["sz399001"])
        & set(breadth_by_date)
        & set(v8_by_date)
    )
    all_breadth_days = [
        breadth_by_date[value] for value in sorted(breadth_by_date)
    ]
    snapshots: list[MarketEnvironmentSnapshot] = []
    state: TrendState = "sideways"
    trend_start: date | None = None
    trend_id = ""
    bull_streak = 0
    bear_streak = 0
    sideways_streak = 0
    warning_triggers: list[_WarningTrigger] = []

    for sequence_index, trading_date in enumerate(common_dates):
        breadth_history = [
            item for item in all_breadth_days if item.trading_date <= trading_date
        ]
        sh_day = index_days["sh000001"][trading_date]
        sz_day = index_days["sz399001"][trading_date]
        warning_triggers.extend(sh_day.warnings)
        warning_triggers.extend(sz_day.warnings)
        breadth = _breadth_evidence(breadth_history)
        capacity = _capacity_evidence(breadth_history)
        contextual = v8_by_date[trading_date]
        directions = (
            sh_day.evidence.fast_direction,
            sz_day.evidence.fast_direction,
        )
        combined_bull = (
            directions == ("bullish", "bullish")
            and breadth.state != "weak"
            and capacity.state != "insufficient"
        )
        combined_bear = directions == ("bearish", "bearish")
        bull_streak = bull_streak + 1 if combined_bull else 0
        bear_streak = bear_streak + 1 if combined_bear else 0
        sideways_signal = not combined_bull and not combined_bear
        sideways_streak = sideways_streak + 1 if sideways_signal else 0
        extreme = combined_bear and _is_extreme_decline(
            breadth_history,
            breadth=breadth,
        )
        next_state = contextual.state
        reasons = [contextual.reason]
        trend_changed = trend_start is None or next_state != state
        if trend_changed:
            state = next_state
            trend_start = trading_date
            trend_id = f"market-env-{trading_date.isoformat()}-{state}"

        if trend_start is None:
            trend_start = trading_date
            trend_id = f"market-env-{trading_date.isoformat()}-{state}"
        active_warnings = _active_warnings(
            warning_triggers,
            trading_date=trading_date,
            sequence_index=sequence_index,
            index_bars=index_bars,
            common_dates=common_dates,
        )
        if active_warnings:
            reasons.append(
                f"存在{len(active_warnings)}条指数形态或背离提示，仅作风险展示"
            )
        snapshots.append(
            MarketEnvironmentSnapshot(
                schema_version="market-environment-snapshot-v2",
                actual_data_date=trading_date,
                rules_version=RULES_VERSION,
                trend_id=trend_id,
                trend_state=state,
                trend_changed=trend_changed,
                trend_start_date=trend_start,
                validation_state="not_required",
                validation_deadline=None,
                fast_bull_streak=bull_streak,
                fast_bear_streak=bear_streak,
                sideways_streak=sideways_streak,
                extreme_decline=extreme,
                contextual_momentum=ContextualMomentumEvidence(
                    baseline_state=contextual.baseline_state,
                    momentum_phase=contextual.phase,
                    momentum_event=contextual.event,
                    prior_directional_streak=contextual.prior_directional_streak,
                    prior_move_atr=round(contextual.prior_move_atr, 6),
                    prior_directional_event_count=(
                        contextual.prior_directional_event_count
                    ),
                    overextended_context=contextual.overextended_context,
                    reversal_candidate=contextual.reversal_candidate,
                    candidate_age=contextual.candidate_age,
                    strong_reversal_verified=(
                        contextual.strong_reversal_verified
                    ),
                    contextual_takeover=contextual.contextual_takeover,
                    active_override=contextual.active_override,
                    released_to_sideways=contextual.released_to_sideways,
                    close_impulse_atr=round(
                        contextual.evidence.close_impulse_atr, 6
                    ),
                    body_impulse_atr=round(
                        contextual.evidence.body_impulse_atr, 6
                    ),
                    close_location=round(contextual.evidence.close_location, 6),
                    advancing_index_count=contextual.evidence.advancing_count,
                    declining_index_count=contextual.evidence.declining_count,
                    bullish_one_atr_count=(
                        contextual.evidence.bullish_one_atr_count
                    ),
                    bearish_one_atr_count=(
                        contextual.evidence.bearish_one_atr_count
                    ),
                ),
                indices=[sh_day.evidence, sz_day.evidence],
                breadth=breadth,
                capacity=capacity,
                cost_pressure=_cost_pressure(
                    index_bars,
                    trading_date=trading_date,
                ),
                warnings=active_warnings,
                reasons=reasons or ["沿用上一交易日市场环境状态"],
                data_sources=[
                    *(index_source(code) for code in V8_INDEX_NAMES),
                    HISTORY_SOURCE,
                ],
                generated_at=(
                    generated_at.astimezone(BEIJING)
                    if generated_at is not None
                    else datetime.combine(
                        trading_date,
                        time(16, 30),
                        tzinfo=BEIJING,
                    )
                ),
            )
        )
    return snapshots


def _load_index_bars(
    path: Path,
    requested_date: date,
) -> dict[str, tuple[str, list[_IndexBar]]]:
    result: dict[str, tuple[str, list[_IndexBar]]] = {}
    for code, name in V8_INDEX_NAMES.items():
        with open_database_connection(path) as connection:
            rows = connection.execute(
                """
                SELECT actual_data_date, open, high, low, close, volume
                FROM historical_benchmark_facts
                WHERE source = ? AND actual_data_date <= ?
                ORDER BY actual_data_date
                """,
                (index_source(code), requested_date.isoformat()),
            ).fetchall()
        if not rows:
            raise MarketEnvironmentUnavailable(f"缺少{name}历史数据")
        result[code] = (
            name,
            [
                _IndexBar(
                    trading_date=date.fromisoformat(cast(str, row[0])),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                )
                for row in rows
            ],
        )
    return result


def _load_main_board_breadth(
    path: Path,
    requested_date: date,
) -> dict[date, _BreadthDay]:
    accumulators: dict[date, _BreadthAccumulator] = defaultdict(
        _BreadthAccumulator
    )
    close_window: deque[float] = deque(maxlen=20)
    active_code = ""
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, code, name, close, previous_close,
                   change_pct, volume, turnover_cny
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date <= ?
              AND listing_trading_days >= 60
              AND volume > 0
              AND name NOT LIKE '%ST%'
              AND (
                   code LIKE '600%' OR code LIKE '601%'
                OR code LIKE '603%' OR code LIKE '605%'
                OR code LIKE '000%' OR code LIKE '001%'
                OR code LIKE '002%' OR code LIKE '003%'
              )
            ORDER BY code, actual_data_date
            """,
            (HISTORY_SOURCE, requested_date.isoformat()),
        )
        for raw in rows:
            trading_date = date.fromisoformat(cast(str, raw[0]))
            code = cast(str, raw[1])
            if code != active_code:
                active_code = code
                close_window = deque(maxlen=20)
            close = float(raw[3])
            previous_close = float(raw[4])
            change_pct = float(raw[5])
            accumulator = accumulators[trading_date]
            accumulator.eligible_count += 1
            accumulator.turnover_cny += int(raw[7])
            if change_pct > 0:
                accumulator.advancers += 1
            elif change_pct < 0:
                accumulator.decliners += 1
            else:
                accumulator.unchanged += 1
            close_window.append(close)
            if len(close_window) == 20:
                if close >= max(close_window):
                    accumulator.new_high_20d += 1
                if close <= min(close_window):
                    accumulator.new_low_20d += 1
            if _at_price_limit(close, previous_close, direction=1):
                accumulator.limit_up += 1
            if _at_price_limit(close, previous_close, direction=-1):
                accumulator.limit_down += 1
    return {
        trading_date: _BreadthDay(
            trading_date=trading_date,
            advancers=value.advancers,
            decliners=value.decliners,
            unchanged=value.unchanged,
            turnover_cny=value.turnover_cny,
            eligible_count=value.eligible_count,
            limit_up=value.limit_up,
            limit_down=value.limit_down,
            new_high_20d=value.new_high_20d,
            new_low_20d=value.new_low_20d,
        )
        for trading_date, value in accumulators.items()
    }


def _calculate_index_days(
    code: str,
    name: str,
    bars: list[_IndexBar],
) -> dict[date, _IndexDay]:
    true_ranges: list[float] = []
    mr20_values: list[float] = []
    rsi_values = _rsi_values(bars)
    confirmed_highs: list[tuple[int, float]] = []
    confirmed_lows: list[tuple[int, float]] = []
    output: dict[date, _IndexDay] = {}
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
        mr20 = statistics.median(true_ranges[max(0, index - 19) : index + 1])
        mr20_values.append(max(mr20, bar.close * 0.0001))
        warnings: list[_WarningTrigger] = []
        pivot_index = index - SWING_RIGHT
        if pivot_index >= SWING_LEFT:
            high_confirmed, low_confirmed = _confirmed_pivot(
                bars,
                mr20_values,
                pivot_index,
            )
            if high_confirmed:
                confirmed_highs.append((pivot_index, bars[pivot_index].high))
                if (
                    len(confirmed_highs) >= 2
                    and rsi_values[confirmed_highs[-2][0]] is not None
                    and rsi_values[confirmed_highs[-1][0]] is not None
                    and confirmed_highs[-1][1] > confirmed_highs[-2][1]
                    and cast(float, rsi_values[confirmed_highs[-1][0]])
                    < cast(float, rsi_values[confirmed_highs[-2][0]])
                ):
                    warnings.append(
                        _warning_trigger(
                            "RSI顶背离",
                            "risk",
                            code,
                            name,
                            index,
                            bar.trading_date,
                            "指数价格摆动高点抬高，但RSI摆动高点降低",
                            confirmed_highs[-1][1],
                            10,
                        )
                    )
            if low_confirmed:
                confirmed_lows.append((pivot_index, bars[pivot_index].low))
                if (
                    len(confirmed_lows) >= 2
                    and rsi_values[confirmed_lows[-2][0]] is not None
                    and rsi_values[confirmed_lows[-1][0]] is not None
                    and confirmed_lows[-1][1] < confirmed_lows[-2][1]
                    and cast(float, rsi_values[confirmed_lows[-1][0]])
                    > cast(float, rsi_values[confirmed_lows[-2][0]])
                ):
                    warnings.append(
                        _warning_trigger(
                            "RSI底背离",
                            "support",
                            code,
                            name,
                            index,
                            bar.trading_date,
                            "指数价格摆动低点降低，但RSI摆动低点抬高",
                            confirmed_lows[-1][1],
                            10,
                        )
                    )
        warnings.extend(_candlestick_warnings(code, name, bars, index))
        if index < MIN_HISTORY_DAYS:
            continue
        nrange = mr20_values[index] / max(bars[index - 1].close, 0.0001)
        return_3d = bar.close / bars[index - 3].close - 1
        return_10d = bar.close / bars[index - 10].close - 1
        slope_5d = _regression_slope(
            [item.close for item in bars[index - 4 : index + 1]]
        ) / mr20_values[index]
        slope_10d = _regression_slope(
            [item.close for item in bars[index - 9 : index + 1]]
        ) / mr20_values[index]
        previous_10 = [item.close for item in bars[index - 10 : index]]
        breakout = bar.close > max(previous_10)
        breakdown = bar.close < min(previous_10)
        held_level = max(
            item.close for item in bars[index - 11 : index - 1]
        )
        held_floor = min(
            item.close for item in bars[index - 11 : index - 1]
        )
        breakout_held = (
            bars[index - 1].close > held_level and bar.close > held_level
        )
        breakdown_held = (
            bars[index - 1].close < held_floor and bar.close < held_floor
        )
        structure = _swing_structure(confirmed_highs, confirmed_lows)
        bull_votes = sum(
            (
                return_3d / nrange >= FAST_RETURN_THRESHOLD,
                slope_5d >= FAST_SLOPE_THRESHOLD,
                breakout,
            )
        )
        bear_votes = sum(
            (
                return_3d / nrange <= -FAST_RETURN_THRESHOLD,
                slope_5d <= -FAST_SLOPE_THRESHOLD,
                breakdown,
            )
        )
        fast_direction: Direction = (
            "bullish" if bull_votes >= 2 else "bearish" if bear_votes >= 2 else "neutral"
        )
        slow_bull_votes = sum(
            (return_10d > 0, slope_10d > 0, structure == "bullish" or breakout_held)
        )
        slow_bear_votes = sum(
            (return_10d < 0, slope_10d < 0, structure == "bearish" or breakdown_held)
        )
        output[bar.trading_date] = _IndexDay(
            evidence=IndexTrendEvidence(
                code=code,
                name=name,
                close=round(bar.close, 4),
                change_pct=round(
                    (bar.close / bars[index - 1].close - 1) * 100,
                    4,
                ),
                fast_direction=fast_direction,
                fast_bull_votes=bull_votes,
                fast_bear_votes=bear_votes,
                return_3d_standardized=round(return_3d / nrange, 4),
                slope_5d_standardized=round(slope_5d, 4),
                breakout_10d=breakout,
                breakdown_10d=breakdown,
                slow_bull_votes=slow_bull_votes,
                slow_bear_votes=slow_bear_votes,
                return_10d_standardized=round(return_10d / nrange, 4),
                slope_10d_standardized=round(slope_10d, 4),
                swing_structure=structure,
                breakout_held_2d=breakout_held,
                breakdown_held_2d=breakdown_held,
            ),
            mr20=mr20_values[index],
            warnings=tuple(warnings),
        )
    return output


def _breadth_evidence(history: list[_BreadthDay]) -> MarketBreadthEvidence:
    current = history[-1]
    recent = history[-5:]
    ratios = [
        item.advancers / item.eligible_count * 100
        for item in recent
        if item.eligible_count
    ]
    average = statistics.mean(ratios) if ratios else 0.0
    positive_days = sum(value > 50 for value in ratios)
    if current.new_high_20d == 0 and current.new_low_20d == 0:
        high_low_ratio = 1.0
    elif current.new_low_20d == 0:
        high_low_ratio = float(current.new_high_20d)
    else:
        high_low_ratio = current.new_high_20d / current.new_low_20d
    strong_votes = sum(
        (average >= 55, high_low_ratio >= 1.5, positive_days >= 4)
    )
    weak_votes = sum(
        (average <= 45, high_low_ratio <= 0.67, positive_days <= 1)
    )
    ratio = (
        current.advancers / current.eligible_count * 100
        if current.eligible_count
        else 0.0
    )
    state: QualityState = (
        "weak"
        if ratio <= 30 and high_low_ratio <= 0.67
        else "strong"
        if ratio >= 70 and high_low_ratio >= 1.5
        else "strong"
        if strong_votes >= 2
        else "weak"
        if weak_votes >= 2
        else "neutral"
    )
    return MarketBreadthEvidence(
        state=state,
        eligible_count=current.eligible_count,
        advancers=current.advancers,
        decliners=current.decliners,
        unchanged=current.unchanged,
        advancer_ratio=round(ratio, 2),
        advancer_ratio_5d_average=round(average, 2),
        positive_breadth_days_5d=positive_days,
        new_high_20d=current.new_high_20d,
        new_low_20d=current.new_low_20d,
        high_low_ratio=round(high_low_ratio, 4),
        strong_votes=strong_votes,
        weak_votes=weak_votes,
    )


def _capacity_evidence(history: list[_BreadthDay]) -> MarketCapacityEvidence:
    current = history[-1]
    median_turnover = int(
        statistics.median(item.turnover_cny for item in history[-20:])
    )
    ratio = current.turnover_cny / median_turnover if median_turnover else 1.0
    state: CapacityState = (
        "abundant" if ratio >= 1.10 else "insufficient" if ratio <= 0.80 else "normal"
    )
    return MarketCapacityEvidence(
        state=state,
        turnover_cny=current.turnover_cny,
        turnover_20d_median_cny=median_turnover,
        ratio_to_20d_median=round(ratio, 4),
    )


def _cost_pressure(
    index_bars: dict[str, tuple[str, list[_IndexBar]]],
    *,
    trading_date: date,
) -> CostPressureProxy:
    upper: list[float] = []
    lower: list[float] = []
    lookbacks: list[int] = []
    for _, bars in index_bars.values():
        eligible = [bar for bar in bars if bar.trading_date <= trading_date][-120:]
        if not eligible:
            continue
        current = eligible[-1].close
        total_weight = sum(max(bar.volume, 1) for bar in eligible)
        upper.append(
            sum(max(bar.volume, 1) for bar in eligible if bar.close > current)
            / total_weight
            * 100
        )
        lower.append(
            sum(max(bar.volume, 1) for bar in eligible if bar.close < current)
            / total_weight
            * 100
        )
        lookbacks.append(len(eligible))
    return CostPressureProxy(
        lookback_days=min(lookbacks, default=0),
        upper_trapped_pressure_pct=round(statistics.mean(upper), 2) if upper else 0,
        lower_profit_pressure_pct=round(statistics.mean(lower), 2) if lower else 0,
        explanation=(
            "按五个主要指数近120个交易日成交量加权收盘位置估算；"
            "不是投资者真实持仓成本，不参与趋势判定"
        ),
    )


def _is_extreme_decline(
    history: list[_BreadthDay],
    *,
    breadth: MarketBreadthEvidence,
) -> bool:
    current = history[-1]
    median_turnover = statistics.median(
        item.turnover_cny for item in history[-20:]
    )
    pressure_votes = sum(
        (
            breadth.advancer_ratio <= 30,
            current.new_low_20d >= max(1, current.new_high_20d * 3),
            current.limit_down > current.limit_up,
            current.turnover_cny >= median_turnover * 1.10,
        )
    )
    return pressure_votes >= 2


def _slow_rise_validated(
    shanghai: IndexTrendEvidence,
    shenzhen: IndexTrendEvidence,
    *,
    breadth: MarketBreadthEvidence,
) -> bool:
    return (
        max(shanghai.slow_bull_votes, shenzhen.slow_bull_votes) >= 2
        and shanghai.fast_direction != "bearish"
        and shenzhen.fast_direction != "bearish"
        and breadth.state != "weak"
    )


def _active_warnings(
    triggers: list[_WarningTrigger],
    *,
    trading_date: date,
    sequence_index: int,
    index_bars: dict[str, tuple[str, list[_IndexBar]]],
    common_dates: list[date],
) -> list[MarketEnvironmentWarning]:
    date_position = {value: index for index, value in enumerate(common_dates)}
    active: list[MarketEnvironmentWarning] = []
    for trigger in triggers:
        trigger_position = date_position.get(trigger.trigger_date)
        if trigger_position is None:
            continue
        age = sequence_index - trigger_position
        if age < 0 or age > trigger.maximum_age:
            continue
        bars = index_bars[trigger.index_code][1]
        closes_after = [
            bar.close
            for bar in bars
            if trigger.trigger_date < bar.trading_date <= trading_date
        ]
        invalidated = (
            any(close > trigger.invalidation_level for close in closes_after)
            if trigger.direction == "risk"
            else any(close < trigger.invalidation_level for close in closes_after)
        )
        if invalidated:
            continue
        identity = (
            f"{trigger.index_code}|{trigger.warning_type}|"
            f"{trigger.trigger_date.isoformat()}"
        )
        active.append(
            MarketEnvironmentWarning(
                warning_id=hashlib.sha256(identity.encode()).hexdigest()[:20],
                warning_type=trigger.warning_type,
                direction=trigger.direction,
                index_code=trigger.index_code,
                index_name=trigger.index_name,
                trigger_date=trigger.trigger_date,
                description=trigger.description,
                invalidation_level=round(trigger.invalidation_level, 4),
            )
        )
    return sorted(active, key=lambda item: (item.trigger_date, item.warning_id))


def _candlestick_warnings(
    code: str,
    name: str,
    bars: list[_IndexBar],
    index: int,
) -> list[_WarningTrigger]:
    if index < 1:
        return []
    previous = bars[index - 1]
    current = bars[index]
    warnings: list[_WarningTrigger] = []
    previous_bull = previous.close > previous.open
    previous_bear = previous.close < previous.open
    current_bull = current.close > current.open
    current_bear = current.close < current.open
    if (
        previous_bull
        and current_bear
        and current.open >= previous.close
        and current.close <= previous.open
    ):
        warnings.append(
            _warning_trigger(
                "看跌吞没",
                "risk",
                code,
                name,
                index,
                current.trading_date,
                "指数阴线实体吞没前一交易日阳线实体",
                current.high,
                5,
            )
        )
    if (
        previous_bear
        and current_bull
        and current.open <= previous.close
        and current.close >= previous.open
    ):
        warnings.append(
            _warning_trigger(
                "看涨吞没",
                "support",
                code,
                name,
                index,
                current.trading_date,
                "指数阳线实体吞没前一交易日阴线实体",
                current.low,
                5,
            )
        )
    if (
        previous_bull
        and current_bear
        and current.open >= previous.close
        and current.close < (previous.open + previous.close) / 2
        and current.close > previous.open
    ):
        warnings.append(
            _warning_trigger(
                "乌云盖顶",
                "risk",
                code,
                name,
                index,
                current.trading_date,
                "指数高开后收至前一阳线实体中点下方",
                current.high,
                5,
            )
        )
    return warnings


def _warning_trigger(
    warning_type: str,
    direction: WarningDirection,
    code: str,
    name: str,
    trigger_index: int,
    trigger_date: date,
    description: str,
    invalidation_level: float,
    maximum_age: int,
) -> _WarningTrigger:
    return _WarningTrigger(
        warning_type=warning_type,
        direction=direction,
        index_code=code,
        index_name=name,
        trigger_index=trigger_index,
        trigger_date=trigger_date,
        description=description,
        invalidation_level=invalidation_level,
        maximum_age=maximum_age,
    )


def _confirmed_pivot(
    bars: list[_IndexBar],
    mr20_values: list[float],
    pivot_index: int,
) -> tuple[bool, bool]:
    left = bars[pivot_index - SWING_LEFT : pivot_index]
    right = bars[pivot_index + 1 : pivot_index + SWING_RIGHT + 1]
    pivot = bars[pivot_index]
    window = [*left, pivot, *right]
    high = (
        pivot.high >= max(item.high for item in left)
        and pivot.high > max(item.high for item in right)
        and pivot.high - min(item.low for item in window)
        >= 0.5 * mr20_values[pivot_index]
    )
    low = (
        pivot.low <= min(item.low for item in left)
        and pivot.low < min(item.low for item in right)
        and max(item.high for item in window) - pivot.low
        >= 0.5 * mr20_values[pivot_index]
    )
    return high, low


def _swing_structure(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
) -> Direction:
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"
    if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
        return "bullish"
    if highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
        return "bearish"
    return "neutral"


def _rsi_values(bars: list[_IndexBar], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return output
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = bars[index].close - bars[index - 1].close
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    output[period] = _rsi(average_gain, average_loss)
    for index in range(period + 1, len(bars)):
        change = bars[index].close - bars[index - 1].close
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        output[index] = _rsi(average_gain, average_loss)
    return output


def _rsi(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def _regression_slope(values: list[float]) -> float:
    count = len(values)
    x_mean = (count - 1) / 2
    y_mean = statistics.mean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    if denominator == 0:
        return 0.0
    return sum(
        (index - x_mean) * (value - y_mean)
        for index, value in enumerate(values)
    ) / denominator


def _at_price_limit(
    close: float,
    previous_close: float,
    *,
    direction: int,
) -> bool:
    if previous_close <= 0:
        return False
    multiplier = Decimal("1.10") if direction > 0 else Decimal("0.90")
    expected = (Decimal(str(previous_close)) * multiplier).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    observed = Decimal(str(close)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return observed == expected


def _publish_snapshots(
    path: Path,
    *,
    snapshots: list[MarketEnvironmentSnapshot],
    run_id: str,
    requested_date: date,
    started_at: datetime,
    completed_at: datetime,
) -> int:
    inserted_count = 0
    with open_database_connection(path) as connection:
        with connection:
            for snapshot in snapshots:
                payload = json.dumps(
                    snapshot.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                digest = hashlib.sha256(payload.encode()).hexdigest()
                existing = connection.execute(
                    """
                    SELECT snapshot_sha256
                    FROM market_environment_snapshots
                    WHERE actual_data_date = ? AND rules_version = ?
                    """,
                    (
                        snapshot.actual_data_date.isoformat(),
                        snapshot.rules_version,
                    ),
                ).fetchone()
                if existing is not None:
                    if cast(str, existing[0]) != digest:
                        raise ValueError(
                            "同一日期和规则版本的市场环境快照内容不一致"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO market_environment_snapshots (
                        actual_data_date, rules_version, trend_state,
                        trend_id, snapshot_sha256, snapshot_json,
                        generated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.actual_data_date.isoformat(),
                        snapshot.rules_version,
                        snapshot.trend_state,
                        snapshot.trend_id,
                        digest,
                        payload,
                        snapshot.generated_at.isoformat(),
                        completed_at.isoformat(),
                    ),
                )
                inserted_count += 1
            connection.execute(
                """
                INSERT INTO market_environment_runs (
                    run_id, requested_date, actual_data_date, rules_version,
                    status, inserted_count, snapshot_count, error_summary,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, 'succeeded', ?, ?, NULL, ?, ?)
                """,
                (
                    run_id,
                    requested_date.isoformat(),
                    snapshots[-1].actual_data_date.isoformat(),
                    RULES_VERSION,
                    inserted_count,
                    len(snapshots),
                    started_at.isoformat(),
                    completed_at.isoformat(),
                ),
            )
    return inserted_count
