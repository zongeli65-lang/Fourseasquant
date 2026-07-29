from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from fourseasquant.akshare_history import HISTORY_SOURCE
from fourseasquant.candlesticks import index_source
from fourseasquant.sqlite_connection import open_database_connection


ALGORITHM_VERSION = "technical-v3"
Board = Literal["main", "chinext", "star"]
DerivativeState = Literal["positive", "zero", "negative"]
StructureState = Literal["forming", "candidate", "strong", "broken"]
TechnicalScoreSortField = Literal[
    "structure_score",
    "breakout_score",
    "relative_strength_score",
]
TechnicalScoreSortOrder = Literal["asc", "desc"]


class TechnicalParameters(BaseModel):
    ema_span: int = Field(default=3, ge=2)
    atr_period: int = Field(default=10, ge=3)
    zero_band_atr: float = Field(default=0.10, gt=0)
    effective_lift_atr: float = Field(default=0.10, gt=0)
    structure_break_pct: float = Field(default=0.02, gt=0, lt=1)
    lift_score_cap_atr: float = Field(default=3.0, gt=0)
    structure_weight: float = 55.0
    breakout_weight: float = 20.0
    relative_strength_weight: float = 15.0
    turnover_weight: float = 10.0
    minimum_leader_score: float = 65.0
    direct_challenge_margin: float = 10.0
    confirmed_challenge_margin: float = 3.0
    challenge_confirmation_days: int = 2
    minimum_sector_members: int = 3


DEFAULT_PARAMETERS = TechnicalParameters()


class ExtremumView(BaseModel):
    kind: Literal["maximum", "minimum"]
    date: date
    value: float


class TechnicalScoreView(BaseModel):
    version: str
    actual_data_date: date
    code: str
    name: str
    board: Board
    ema3: float
    derivative: float
    derivative_state: DerivativeState
    zero_threshold: float
    atr10: float
    structure_state: StructureState
    structure_valid: bool
    active_breakout: bool
    structure_score: float
    breakout_score: float
    relative_strength_score: float
    turnover_score: float
    total_score: float
    maxima: list[ExtremumView]
    minima: list[ExtremumView]
    evidence: dict[str, object]


class TechnicalScorePublication(BaseModel):
    version: str
    official_start: date
    official_end: date
    qfq_source: str
    symbol_count: int
    score_count: int
    published_at: datetime


class TechnicalScoreStatus(BaseModel):
    status: Literal["not_initialized", "ready"]
    publication: TechnicalScorePublication | None
    parameters: TechnicalParameters


class TechnicalScoreListItem(TechnicalScoreView):
    rank: int | None
    is_current: bool


class TechnicalScorePage(BaseModel):
    requested_date: date
    actual_data_date: date | None
    total: int
    universe_count: int
    current_score_count: int
    stale_score_count: int
    page: int
    page_size: int
    items: list[TechnicalScoreListItem]


@dataclass(frozen=True)
class _PriceRow:
    trading_date: date
    code: str
    name: str
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    turnover_cny: int


@dataclass
class _Extremum:
    kind: Literal["maximum", "minimum"]
    trading_date: date
    value: float
    atr10: float


def score_technical_history(
    path: Path,
    *,
    official_start: date,
    official_end: date,
    qfq_source: str,
    parameters: TechnicalParameters = DEFAULT_PARAMETERS,
    version: str = ALGORITHM_VERSION,
    published_at: datetime | None = None,
    activate: bool = True,
) -> TechnicalScorePublication:
    """Compute a complete score batch and optionally make it reader-visible."""
    rows = _load_price_rows(path, qfq_source, official_end)
    if not rows:
        raise ValueError("前复权行情为空，无法初始化技术评分")
    benchmark_closes = _load_benchmark_closes(path, official_end)
    grouped = _group_rows(rows)
    publication_time = published_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    parameters_json = parameters.model_dump_json()
    score_count = 0

    with open_database_connection(path) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO technical_score_versions (
                    version, parameters_json, created_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(version) DO NOTHING
                """,
                (version, parameters_json, publication_time.isoformat()),
            )
            stored_parameters = connection.execute(
                """
                SELECT parameters_json
                FROM technical_score_versions
                WHERE version = ?
                """,
                (version,),
            ).fetchone()
            if stored_parameters is None or stored_parameters[0] != parameters_json:
                raise ValueError("同一技术算法版本不允许修改参数")
            connection.execute(
                """
                DELETE FROM technical_daily_scores
                WHERE version = ? AND qfq_source = ?
                  AND actual_data_date BETWEEN ? AND ?
                """,
                (
                    version,
                    qfq_source,
                    official_start.isoformat(),
                    official_end.isoformat(),
                ),
            )
            for code, history in grouped.items():
                calculated = _score_symbol(
                    history,
                    benchmark_closes=benchmark_closes,
                    official_start=official_start,
                    official_end=official_end,
                    qfq_source=qfq_source,
                    parameters=parameters,
                    version=version,
                )
                connection.executemany(
                    """
                    INSERT INTO technical_daily_scores (
                        version, actual_data_date, code, name, board,
                        qfq_source, ema3, derivative, derivative_state,
                        zero_threshold, atr10, structure_state,
                        structure_valid, active_breakout, structure_score,
                        breakout_score, relative_strength_score,
                        turnover_score, total_score, extrema_json,
                        evidence_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (_database_values(item, qfq_source) for item in calculated),
                )
                score_count += len(calculated)
            if score_count == 0:
                raise ValueError("正式区间没有可发布的技术评分")
            if activate:
                connection.execute(
                    """
                    INSERT INTO technical_score_publications (
                        version, official_start, official_end, qfq_source,
                        symbol_count, score_count, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(version, official_start, official_end)
                    DO UPDATE SET
                        qfq_source = excluded.qfq_source,
                        symbol_count = excluded.symbol_count,
                        score_count = excluded.score_count,
                        published_at = excluded.published_at
                    """,
                    (
                        version,
                        official_start.isoformat(),
                        official_end.isoformat(),
                        qfq_source,
                        len(grouped),
                        score_count,
                        publication_time.isoformat(),
                    ),
                )
    return TechnicalScorePublication(
        version=version,
        official_start=official_start,
        official_end=official_end,
        qfq_source=qfq_source,
        symbol_count=len(grouped),
        score_count=score_count,
        published_at=publication_time,
    )


def read_technical_score_status(path: Path) -> TechnicalScoreStatus:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT version, official_start, official_end, qfq_source,
                   symbol_count, score_count, published_at
            FROM technical_score_publications
            ORDER BY official_end DESC, published_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return TechnicalScoreStatus(
                status="not_initialized",
                publication=None,
                parameters=DEFAULT_PARAMETERS,
            )
        version_row = connection.execute(
            """
            SELECT parameters_json
            FROM technical_score_versions
            WHERE version = ?
            """,
            (row[0],),
        ).fetchone()
    parameters = (
        TechnicalParameters.model_validate_json(cast(str, version_row[0]))
        if version_row
        else DEFAULT_PARAMETERS
    )
    return TechnicalScoreStatus(
        status="ready",
        publication=_publication_from_row(row),
        parameters=parameters,
    )


def read_top_technical_scores(
    path: Path,
    *,
    requested_date: date,
    limit: int = 20,
    sort_by: TechnicalScoreSortField = "structure_score",
) -> list[TechnicalScoreView]:
    status = read_technical_score_status(path)
    if status.publication is None:
        return []
    version = status.publication.version
    with open_database_connection(path) as connection:
        actual_row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM technical_daily_scores
            WHERE version = ? AND qfq_source = ? AND actual_data_date <= ?
            """,
            (
                version,
                status.publication.qfq_source,
                requested_date.isoformat(),
            ),
        ).fetchone()
        if actual_row is None or actual_row[0] is None:
            return []
        sort_column = {
            "structure_score": "structure_score",
            "breakout_score": "breakout_score",
            "relative_strength_score": "relative_strength_score",
        }[sort_by]
        ranking_order = (
            f"{sort_column} DESC, turnover_score DESC"
            if sort_by in {"breakout_score", "relative_strength_score"}
            else f"{sort_column} DESC"
        )
        rows = connection.execute(
            f"""
            SELECT version, actual_data_date, code, name, board, ema3,
                   derivative, derivative_state, zero_threshold, atr10,
                   structure_state, structure_valid, active_breakout,
                   structure_score, breakout_score, relative_strength_score,
                   turnover_score, total_score, extrema_json, evidence_json
            FROM technical_daily_scores
            WHERE version = ? AND qfq_source = ? AND actual_data_date = ?
            ORDER BY {ranking_order}, code
            LIMIT ?
            """,
            (
                version,
                status.publication.qfq_source,
                cast(str, actual_row[0]),
                max(1, min(limit, 100)),
            ),
        ).fetchall()
    return [_score_from_database_row(row) for row in rows]


def read_technical_score_page(
    path: Path,
    *,
    requested_date: date,
    page: int = 1,
    page_size: int = 100,
    search: str = "",
    board: Board | None = None,
    sort_by: TechnicalScoreSortField = "structure_score",
    sort_order: TechnicalScoreSortOrder = "desc",
) -> TechnicalScorePage:
    status = read_technical_score_status(path)
    if status.publication is None:
        return TechnicalScorePage(
            requested_date=requested_date,
            actual_data_date=None,
            total=0,
            universe_count=0,
            current_score_count=0,
            stale_score_count=0,
            page=max(1, page),
            page_size=max(1, min(page_size, 100)),
            items=[],
        )
    version = status.publication.version
    qfq_source = status.publication.qfq_source
    selected_page = max(1, page)
    selected_page_size = max(1, min(page_size, 100))
    normalized_search = search.strip()
    with open_database_connection(path) as connection:
        actual_row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM technical_daily_scores
            WHERE version = ? AND qfq_source = ? AND actual_data_date <= ?
            """,
            (version, qfq_source, requested_date.isoformat()),
        ).fetchone()
        if actual_row is None or actual_row[0] is None:
            return TechnicalScorePage(
                requested_date=requested_date,
                actual_data_date=None,
                total=0,
                universe_count=0,
                current_score_count=0,
                stale_score_count=0,
                page=selected_page,
                page_size=selected_page_size,
                items=[],
            )
        actual_data_date = date.fromisoformat(cast(str, actual_row[0]))
        stale_scores = _read_stale_scores(
            connection,
            version=version,
            qfq_source=qfq_source,
            actual_data_date=actual_data_date,
        )
        current_score_count = cast(
            int,
            connection.execute(
                """
                SELECT COUNT(*)
                FROM technical_daily_scores
                WHERE version = ? AND qfq_source = ? AND actual_data_date = ?
                """,
                (version, qfq_source, actual_data_date.isoformat()),
            ).fetchone()[0],
        )
        filtered_stale = _filter_stale_scores(
            stale_scores,
            search=normalized_search,
            board=board,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        where_sql, where_parameters = _technical_score_filters(
            normalized_search,
            board,
        )
        filtered_current_count = cast(
            int,
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM technical_daily_scores
                WHERE version = ? AND qfq_source = ? AND actual_data_date = ?
                  {where_sql}
                """,
                (
                    version,
                    qfq_source,
                    actual_data_date.isoformat(),
                    *where_parameters,
                ),
            ).fetchone()[0],
        )
        offset = (selected_page - 1) * selected_page_size
        current_items: list[TechnicalScoreListItem] = []
        if offset < filtered_current_count:
            current_limit = min(
                selected_page_size,
                filtered_current_count - offset,
            )
            current_items = _read_current_score_items(
                connection,
                version=version,
                qfq_source=qfq_source,
                actual_data_date=actual_data_date,
                where_sql=where_sql,
                where_parameters=where_parameters,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=current_limit,
                offset=offset,
            )
        stale_offset = max(0, offset - filtered_current_count)
        remaining = selected_page_size - len(current_items)
        stale_items = [
            TechnicalScoreListItem(
                **score.model_dump(),
                rank=None,
                is_current=False,
            )
            for score in filtered_stale[stale_offset : stale_offset + remaining]
        ]
    stale_score_count = len(stale_scores)
    return TechnicalScorePage(
        requested_date=requested_date,
        actual_data_date=actual_data_date,
        total=filtered_current_count + len(filtered_stale),
        universe_count=current_score_count + stale_score_count,
        current_score_count=current_score_count,
        stale_score_count=stale_score_count,
        page=selected_page,
        page_size=selected_page_size,
        items=[*current_items, *stale_items],
    )


def _technical_score_filters(
    search: str,
    board: Board | None,
) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if search:
        escaped = (
            search.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        clauses.append(
            "(code LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')"
        )
        parameters.extend((pattern, pattern))
    if board is not None:
        clauses.append("board = ?")
        parameters.append(board)
    return (
        f"AND {' AND '.join(clauses)}" if clauses else "",
        tuple(parameters),
    )


def _read_current_score_items(
    connection: sqlite3.Connection,
    *,
    version: str,
    qfq_source: str,
    actual_data_date: date,
    where_sql: str,
    where_parameters: tuple[object, ...],
    sort_by: TechnicalScoreSortField,
    sort_order: TechnicalScoreSortOrder,
    limit: int,
    offset: int,
) -> list[TechnicalScoreListItem]:
    sort_columns = {
        "structure_score": "structure_score",
        "breakout_score": "breakout_score",
        "relative_strength_score": "relative_strength_score",
    }
    sort_column = sort_columns[sort_by]
    direction = "ASC" if sort_order == "asc" else "DESC"
    ranking_order = (
        f"{sort_column} DESC, turnover_score DESC"
        if sort_by in {"breakout_score", "relative_strength_score"}
        else f"{sort_column} DESC"
    )
    display_order = (
        f"{sort_column} {direction}, turnover_score {direction}"
        if sort_by in {"breakout_score", "relative_strength_score"}
        else f"{sort_column} {direction}"
    )
    rows = connection.execute(
        f"""
        WITH ranked AS (
            SELECT version, actual_data_date, code, name, board, ema3,
                   derivative, derivative_state, zero_threshold, atr10,
                   structure_state, structure_valid, active_breakout,
                   structure_score, breakout_score, relative_strength_score,
                   turnover_score, total_score, extrema_json, evidence_json,
                   DENSE_RANK() OVER (
                       ORDER BY {ranking_order}
                   ) AS market_rank
            FROM technical_daily_scores
            WHERE version = ? AND qfq_source = ? AND actual_data_date = ?
        )
        SELECT version, actual_data_date, code, name, board, ema3,
               derivative, derivative_state, zero_threshold, atr10,
               structure_state, structure_valid, active_breakout,
               structure_score, breakout_score, relative_strength_score,
               turnover_score, total_score, extrema_json, evidence_json,
               market_rank
        FROM ranked
        WHERE 1 = 1 {where_sql}
        ORDER BY {display_order}, code ASC
        LIMIT ? OFFSET ?
        """,
        (
            version,
            qfq_source,
            actual_data_date.isoformat(),
            *where_parameters,
            limit,
            offset,
        ),
    ).fetchall()
    return [
        TechnicalScoreListItem(
            **_score_from_database_row(row[:20]).model_dump(),
            rank=int(row[20]),
            is_current=True,
        )
        for row in rows
    ]


def _read_stale_scores(
    connection: sqlite3.Connection,
    *,
    version: str,
    qfq_source: str,
    actual_data_date: date,
) -> list[TechnicalScoreView]:
    missing_rows = connection.execute(
        """
        SELECT code
        FROM technical_daily_scores
        WHERE version = ? AND qfq_source = ? AND actual_data_date <= ?
        EXCEPT
        SELECT code
        FROM technical_daily_scores
        WHERE version = ? AND qfq_source = ? AND actual_data_date = ?
        """,
        (
            version,
            qfq_source,
            actual_data_date.isoformat(),
            version,
            qfq_source,
            actual_data_date.isoformat(),
        ),
    ).fetchall()
    rows = [
        row
        for (code,) in missing_rows
        if (
            row := connection.execute(
                """
                SELECT version, actual_data_date, code, name, board, ema3,
                       derivative, derivative_state, zero_threshold, atr10,
                       structure_state, structure_valid, active_breakout,
                       structure_score, breakout_score,
                       relative_strength_score, turnover_score, total_score,
                       extrema_json, evidence_json
                FROM technical_daily_scores
                WHERE version = ? AND qfq_source = ? AND code = ?
                  AND actual_data_date <= ?
                ORDER BY actual_data_date DESC
                LIMIT 1
                """,
                (
                    version,
                    qfq_source,
                    code,
                    actual_data_date.isoformat(),
                ),
            ).fetchone()
        )
        is not None
    ]
    return [_score_from_database_row(row) for row in rows]


def _filter_stale_scores(
    scores: list[TechnicalScoreView],
    *,
    search: str,
    board: Board | None,
    sort_by: TechnicalScoreSortField,
    sort_order: TechnicalScoreSortOrder,
) -> list[TechnicalScoreView]:
    normalized = search.casefold()
    filtered = [
        score
        for score in scores
        if (board is None or score.board == board)
        and (
            not normalized
            or normalized in score.code.casefold()
            or normalized in score.name.casefold()
        )
    ]
    filtered.sort(key=lambda score: score.code)
    if sort_by in {"breakout_score", "relative_strength_score"}:
        filtered.sort(
            key=lambda score: (
                getattr(score, sort_by),
                score.turnover_score,
            ),
            reverse=sort_order == "desc",
        )
    else:
        filtered.sort(
            key=lambda score: getattr(score, sort_by),
            reverse=sort_order == "desc",
        )
    return filtered


def _load_price_rows(
    path: Path, qfq_source: str, official_end: date
) -> list[_PriceRow]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, code, name, open, high, low, close,
                   previous_close, turnover_cny
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date <= ?
              AND listing_trading_days >= 60
            ORDER BY code, actual_data_date
            """,
            (qfq_source, official_end.isoformat()),
        ).fetchall()
    return [
        _PriceRow(
            trading_date=date.fromisoformat(cast(str, row[0])),
            code=cast(str, row[1]),
            name=cast(str, row[2]),
            open=float(row[3]),
            high=float(row[4]),
            low=float(row[5]),
            close=float(row[6]),
            previous_close=float(row[7]),
            turnover_cny=int(row[8]),
        )
        for row in rows
    ]


def _load_benchmark_closes(
    path: Path, official_end: date
) -> dict[str, dict[date, float]]:
    symbols = ("sh000001", "sz399001", "sh000300", "sz399006", "sh000688")
    result: dict[str, dict[date, float]] = {}
    with open_database_connection(path) as connection:
        for symbol in symbols:
            rows = connection.execute(
                """
                SELECT actual_data_date, close
                FROM historical_benchmark_facts
                WHERE source = ? AND actual_data_date <= ?
                ORDER BY actual_data_date
                """,
                (index_source(symbol), official_end.isoformat()),
            ).fetchall()
            if not rows:
                raise ValueError(f"缺少指数历史数据：{symbol}")
            result[symbol] = {
                date.fromisoformat(cast(str, row[0])): float(row[1])
                for row in rows
            }
    return result


def _group_rows(rows: Iterable[_PriceRow]) -> dict[str, list[_PriceRow]]:
    grouped: dict[str, list[_PriceRow]] = {}
    for row in rows:
        grouped.setdefault(row.code, []).append(row)
    return grouped


def _score_symbol(
    rows: list[_PriceRow],
    *,
    benchmark_closes: dict[str, dict[date, float]],
    official_start: date,
    official_end: date,
    qfq_source: str,
    parameters: TechnicalParameters,
    version: str,
) -> list[TechnicalScoreView]:
    del qfq_source
    alpha = 2 / (parameters.ema_span + 1)
    ema_values: list[float] = []
    atr_values: list[float] = []
    true_ranges: list[float] = []
    maxima: list[_Extremum] = []
    minima: list[_Extremum] = []
    last_trend: int | None = None
    segment_index = 0
    breakout_high = 0.0
    breakout_streak = 0
    structure_was_valid = False
    structure_broken_latched = False
    structure_break_reason: str | None = None
    structure_break_reference: float | None = None
    structure_break_line: float | None = None
    scores: list[TechnicalScoreView] = []

    for index, row in enumerate(rows):
        ema = (
            row.close
            if index == 0
            else alpha * row.close + (1 - alpha) * ema_values[-1]
        )
        ema_values.append(ema)
        previous_close = rows[index - 1].close if index else row.previous_close
        true_range = max(
            row.high - row.low,
            abs(row.high - previous_close),
            abs(row.low - previous_close),
        )
        true_ranges.append(true_range)
        atr_window = true_ranges[
            max(0, index - parameters.atr_period + 1) : index + 1
        ]
        atr = max(sum(atr_window) / len(atr_window), row.close * 0.0001)
        atr_values.append(atr)
        derivative = ema - ema_values[index - 1] if index else 0.0
        zero_threshold = atr * parameters.zero_band_atr
        trend = 1 if derivative > zero_threshold else -1 if derivative < -zero_threshold else 0

        last_trough = minima[-1] if minima else None
        last_trough_value = (
            last_trough.value if last_trough is not None else None
        )
        current_break_line = (
            last_trough_value * (1 - parameters.structure_break_pct)
            if last_trough_value is not None
            else None
        )
        just_broke_structure = (
            structure_was_valid
            and last_trough_value is not None
            and current_break_line is not None
            and ema < current_break_line
        )
        if just_broke_structure:
            structure_broken_latched = True
            structure_break_reason = "ema_below_last_trough"
            structure_break_reference = last_trough_value
            structure_break_line = current_break_line
            maxima.clear()
            minima.clear()
            last_trend = trend if trend != 0 else -1
            segment_index = index
            breakout_high = 0.0
            breakout_streak = 0
        confirmed_extremum: _Extremum | None = None
        if not just_broke_structure and trend != 0:
            if last_trend is None:
                last_trend = trend
                segment_index = index
            elif trend == last_trend:
                segment_index = _updated_segment_index(
                    ema_values, segment_index, index, trend
                )
            else:
                kind: Literal["maximum", "minimum"] = (
                    "maximum" if last_trend > 0 else "minimum"
                )
                extremum = _Extremum(
                    kind=kind,
                    trading_date=rows[segment_index].trading_date,
                    value=ema_values[segment_index],
                    atr10=atr_values[segment_index],
                )
                (maxima if kind == "maximum" else minima).append(extremum)
                confirmed_extremum = extremum
                last_trend = trend
                segment_index = index
        elif not just_broke_structure and last_trend is not None:
            segment_index = _updated_segment_index(
                ema_values, segment_index, index, last_trend
            )

        peak_lifts = _normalized_lifts(maxima, atr)
        trough_lifts = _normalized_lifts(minima, atr)
        structure_valid = (
            bool(peak_lifts)
            and bool(trough_lifts)
            and peak_lifts[-1] > parameters.effective_lift_atr
            and trough_lifts[-1] > parameters.effective_lift_atr
        )
        strong = (
            len(peak_lifts) >= 2
            and len(trough_lifts) >= 2
            and all(
                lift > parameters.effective_lift_atr
                for lift in peak_lifts[-2:] + trough_lifts[-2:]
            )
        )
        broken = (
            bool(peak_lifts)
            and bool(trough_lifts)
            and (
                peak_lifts[-1] < -parameters.effective_lift_atr
                or trough_lifts[-1] < -parameters.effective_lift_atr
            )
        )
        if structure_valid:
            structure_broken_latched = False
            structure_break_reason = None
            structure_break_reference = None
            structure_break_line = None
        elif broken:
            structure_broken_latched = True
            structure_break_reason = "extrema_stopped_rising"
            if confirmed_extremum is not None:
                maxima = (
                    [confirmed_extremum]
                    if confirmed_extremum.kind == "maximum"
                    else []
                )
                minima = (
                    [confirmed_extremum]
                    if confirmed_extremum.kind == "minimum"
                    else []
                )
        structure_state: StructureState = (
            "broken"
            if structure_broken_latched
            else "strong"
            if strong
            else "candidate"
            if structure_valid
            else "forming"
        )
        structure_score = (
            _structure_score(peak_lifts, trough_lifts, parameters)
            if structure_valid
            else 0.0
        )
        structure_was_valid = structure_valid

        last_peak = maxima[-1].value if maxima else None
        active_breakout = (
            last_peak is not None
            and trend > 0
            and ema > last_peak + zero_threshold
        )
        if active_breakout and structure_valid:
            if breakout_streak == 0 or ema >= breakout_high:
                breakout_streak += 1
                breakout_high = ema
            else:
                breakout_streak = 0
            distance = (ema - cast(float, last_peak)) / atr
            breakout_score = (
                parameters.breakout_weight * 0.75
                * min(max(distance, 0.0), parameters.lift_score_cap_atr)
                / parameters.lift_score_cap_atr
                + parameters.breakout_weight
                * 0.25
                * min(breakout_streak, 5)
                / 5
            )
        else:
            breakout_high = 0.0
            breakout_streak = 0
            breakout_score = 0.0

        relative_score = _relative_strength_score(
            rows,
            index,
            board=_board_for_code(row.code),
            benchmark_closes=benchmark_closes,
            weight=parameters.relative_strength_weight,
        )
        turnover_score = _turnover_score(
            rows, index, parameters.turnover_weight
        )
        total_score = min(
            100.0,
            structure_score
            + breakout_score
            + relative_score
            + turnover_score,
        )
        if not official_start <= row.trading_date <= official_end:
            continue
        scores.append(
            TechnicalScoreView(
                version=version,
                actual_data_date=row.trading_date,
                code=row.code,
                name=row.name,
                board=_board_for_code(row.code),
                ema3=round(ema, 6),
                derivative=round(derivative, 6),
                derivative_state=_derivative_state(trend),
                zero_threshold=round(zero_threshold, 6),
                atr10=round(atr, 6),
                structure_state=structure_state,
                structure_valid=structure_valid,
                active_breakout=active_breakout,
                structure_score=round(structure_score, 4),
                breakout_score=round(breakout_score, 4),
                relative_strength_score=round(relative_score, 4),
                turnover_score=round(turnover_score, 4),
                total_score=round(total_score, 4),
                maxima=_extrema_views(maxima[-3:]),
                minima=_extrema_views(minima[-3:]),
                evidence={
                    "peak_lifts_atr": [round(value, 4) for value in peak_lifts[-2:]],
                    "trough_lifts_atr": [
                        round(value, 4) for value in trough_lifts[-2:]
                    ],
                    "breakout_streak": breakout_streak,
                    "minimum_leader_score": parameters.minimum_leader_score,
                    "structure_break_pct": parameters.structure_break_pct,
                    "structure_break_reason": structure_break_reason,
                    "structure_break_reference": (
                        round(structure_break_reference, 6)
                        if structure_break_reference is not None
                        else None
                    ),
                    "structure_break_line": (
                        round(structure_break_line, 6)
                        if structure_break_line is not None
                        else None
                    ),
                },
            )
        )
    return scores


def _updated_segment_index(
    values: list[float], current: int, candidate: int, trend: int
) -> int:
    if trend > 0 and values[candidate] >= values[current]:
        return candidate
    if trend < 0 and values[candidate] <= values[current]:
        return candidate
    return current


def _normalized_lifts(
    extrema: list[_Extremum], _current_atr: float
) -> list[float]:
    return [
        (extrema[index].value - extrema[index - 1].value)
        / max(extrema[index].atr10, extrema[index - 1].atr10)
        for index in range(1, len(extrema))
    ]


def _structure_score(
    peak_lifts: list[float],
    trough_lifts: list[float],
    parameters: TechnicalParameters,
) -> float:
    def quality(values: list[float]) -> float:
        if not values:
            return 0.0
        recent = values[-2:]
        return sum(
            min(max(value, 0.0), parameters.lift_score_cap_atr)
            / parameters.lift_score_cap_atr
            for value in recent
        ) / len(recent)

    return parameters.structure_weight * (
        quality(peak_lifts) + quality(trough_lifts)
    ) / 2


def _relative_strength_score(
    rows: list[_PriceRow],
    index: int,
    *,
    board: Board,
    benchmark_closes: dict[str, dict[date, float]],
    weight: float,
) -> float:
    broad = benchmark_closes["sh000300"]
    board_symbol = _board_benchmark(rows[index].code, board)
    board_prices = benchmark_closes[board_symbol]
    horizons = ((3, 5.0), (5, 8.0), (10, 12.0))
    broad_scores: list[float] = []
    board_scores: list[float] = []
    for horizon, scale in horizons:
        if index < horizon:
            continue
        current = rows[index]
        previous = rows[index - horizon]
        stock_return = (current.close / previous.close - 1) * 100
        broad_return = _benchmark_return(
            broad, current.trading_date, previous.trading_date
        )
        board_return = _benchmark_return(
            board_prices, current.trading_date, previous.trading_date
        )
        broad_scores.append(_scaled_excess(stock_return - broad_return, scale))
        board_scores.append(_scaled_excess(stock_return - board_return, scale))
    if not broad_scores:
        return 0.0
    return weight * (
        sum(broad_scores) / len(broad_scores)
        + sum(board_scores) / len(board_scores)
    ) / 2


def _benchmark_return(
    closes: dict[date, float], current: date, previous: date
) -> float:
    current_close = closes.get(current)
    previous_close = closes.get(previous)
    if current_close is None or previous_close is None or previous_close <= 0:
        raise ValueError(f"指数在 {previous} 至 {current} 的数据不完整")
    return (current_close / previous_close - 1) * 100


def _scaled_excess(excess_pct: float, scale_pct: float) -> float:
    return 0.5 + 0.5 * excess_pct / (abs(excess_pct) + scale_pct)


def _turnover_score(
    rows: list[_PriceRow], index: int, weight: float
) -> float:
    previous = rows[max(0, index - 10) : index]
    if not previous:
        return 0.0
    average = sum(row.turnover_cny for row in previous) / len(previous)
    if average <= 0:
        return 0.0
    ratio = rows[index].turnover_cny / average
    return weight * min(max(ratio / 2, 0.0), 1.0)


def _board_for_code(code: str) -> Board:
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    return "main"


def _board_benchmark(code: str, board: Board) -> str:
    if board == "chinext":
        return "sz399006"
    if board == "star":
        return "sh000688"
    return "sh000001" if code.startswith("6") else "sz399001"


def _derivative_state(trend: int) -> DerivativeState:
    return "positive" if trend > 0 else "negative" if trend < 0 else "zero"


def _extrema_views(extrema: list[_Extremum]) -> list[ExtremumView]:
    return [
        ExtremumView(
            kind=item.kind,
            date=item.trading_date,
            value=round(item.value, 6),
        )
        for item in extrema
    ]


def _database_values(
    score: TechnicalScoreView, qfq_source: str
) -> tuple[object, ...]:
    extrema_json = json.dumps(
        {
            "maxima": [
                item.model_dump(mode="json") for item in score.maxima
            ],
            "minima": [
                item.model_dump(mode="json") for item in score.minima
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        score.version,
        score.actual_data_date.isoformat(),
        score.code,
        score.name,
        score.board,
        qfq_source,
        score.ema3,
        score.derivative,
        score.derivative_state,
        score.zero_threshold,
        score.atr10,
        score.structure_state,
        int(score.structure_valid),
        int(score.active_breakout),
        score.structure_score,
        score.breakout_score,
        score.relative_strength_score,
        score.turnover_score,
        score.total_score,
        extrema_json,
        json.dumps(score.evidence, ensure_ascii=False, sort_keys=True),
    )


def _publication_from_row(row: tuple[object, ...]) -> TechnicalScorePublication:
    return TechnicalScorePublication(
        version=cast(str, row[0]),
        official_start=date.fromisoformat(cast(str, row[1])),
        official_end=date.fromisoformat(cast(str, row[2])),
        qfq_source=cast(str, row[3]),
        symbol_count=int(str(row[4])),
        score_count=int(str(row[5])),
        published_at=datetime.fromisoformat(cast(str, row[6])),
    )


def _score_from_database_row(row: tuple[object, ...]) -> TechnicalScoreView:
    extrema = json.loads(cast(str, row[18]))
    return TechnicalScoreView(
        version=cast(str, row[0]),
        actual_data_date=date.fromisoformat(cast(str, row[1])),
        code=cast(str, row[2]),
        name=cast(str, row[3]),
        board=cast(Board, row[4]),
        ema3=float(str(row[5])),
        derivative=float(str(row[6])),
        derivative_state=cast(DerivativeState, row[7]),
        zero_threshold=float(str(row[8])),
        atr10=float(str(row[9])),
        structure_state=cast(StructureState, row[10]),
        structure_valid=bool(row[11]),
        active_breakout=bool(row[12]),
        structure_score=float(str(row[13])),
        breakout_score=float(str(row[14])),
        relative_strength_score=float(str(row[15])),
        turnover_score=float(str(row[16])),
        total_score=float(str(row[17])),
        maxima=[
            ExtremumView.model_validate(item)
            for item in extrema.get("maxima", [])
        ],
        minima=[
            ExtremumView.model_validate(item)
            for item in extrema.get("minima", [])
        ],
        evidence=cast(
            dict[str, object], json.loads(cast(str, row[19]))
        ),
    )
