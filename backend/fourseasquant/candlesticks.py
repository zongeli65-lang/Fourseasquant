from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from fourseasquant.akshare_history import HISTORY_QFQ_SOURCE, HISTORY_SOURCE
from fourseasquant.database import (
    HistoricalBenchmarkFactRow,
    active_automation_claim_exists,
    latest_task_run,
    save_historical_benchmark_fact,
)
from fourseasquant.sqlite_connection import open_database_connection


INDEXES: dict[str, str] = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sh000300": "沪深 300",
    "sz399006": "创业板指",
    "sh000688": "科创 50",
}
INDEX_SOURCE_PREFIX = "akshare_index_"
IndexHistoryFactory = Callable[[str], pd.DataFrame]


class SecuritySearchResult(BaseModel):
    code: str
    name: str
    exchange: Literal["上海", "深圳"]


class CandlePoint(BaseModel):
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    turnover_cny: int = Field(ge=0)
    change_pct: float
    rsi14: float | None


class TradeMarker(BaseModel):
    date: date
    side: Literal["buy", "sell"]
    price: float = Field(gt=0)
    amount_cny: float = Field(gt=0)


class CandleSeries(BaseModel):
    source: Literal["akshare"] = "akshare"
    instrument_type: Literal["stock", "index"]
    code: str
    name: str
    adjustment: Literal["raw", "qfq"]
    requested_date: date
    actual_data_date: date
    coverage_start: date
    coverage_end: date
    candles: list[CandlePoint]
    trades: list[TradeMarker]


class CandleAvailability(BaseModel):
    requested_date: date
    latest_published_date: date | None
    status: Literal["ready", "updating", "stale", "unavailable"]
    message: str


class CandleDataNotFound(RuntimeError):
    pass


def index_source(symbol: str, *, version_tag: str | None = None) -> str:
    source = f"{INDEX_SOURCE_PREFIX}{symbol}"
    return f"{source}:{version_tag}" if version_tag else source


def import_index_candles(
    path: Path,
    *,
    requested_end_date: date,
    index_history: IndexHistoryFactory,
    warmup_trading_days: int = 0,
    version_tag: str | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for symbol, name in INDEXES.items():
        frame = index_history(symbol).copy()
        if frame.empty:
            raise CandleDataNotFound(f"{name} 没有返回历史数据")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame[frame["date"] <= requested_end_date]
        frame = (
            frame.sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        if frame.empty:
            raise CandleDataNotFound(f"{name} 在目标日期前没有历史数据")
        range_end = cast(date, frame.iloc[-1]["date"])
        official_start = _one_year_start(range_end)
        official_rows = frame[frame["date"] >= official_start]
        if official_rows.empty:
            raise CandleDataNotFound(f"{name} 最近一年没有历史数据")
        official_index = int(official_rows.index[0])
        start_index = max(0, official_index - max(0, warmup_trading_days))
        range_start = cast(date, frame.loc[start_index, "date"])
        rows = frame[frame["date"] >= range_start].to_dict("records")
        for row in rows:
            trading_date = _date_value(row.get("date"))
            if trading_date is None:
                continue
            save_historical_benchmark_fact(
                path,
                source=index_source(symbol, version_tag=version_tag),
                fact=HistoricalBenchmarkFactRow(
                    actual_data_date=trading_date,
                    name=name,
                    open=_number(row.get("open")),
                    high=_number(row.get("high")),
                    low=_number(row.get("low")),
                    close=_number(row.get("close")),
                    volume=_integer(row.get("volume")),
                ),
            )
        counts[symbol] = len(rows)
    return counts


def publish_complete_candle_dates(
    path: Path,
    *,
    qfq_source: str = HISTORY_QFQ_SOURCE,
    published_at: datetime | None = None,
    publication_start: date | None = None,
) -> int:
    publication_time = published_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    with open_database_connection(path) as connection:
        with connection:
            return _publish_complete_candle_dates(
                connection,
                qfq_source=qfq_source,
                published_at=publication_time,
                publication_start=publication_start,
            )


def promote_staged_candle_dates(
    path: Path,
    *,
    raw_staging_source: str,
    qfq_staging_source: str,
    qfq_source: str,
    index_version_tag: str,
    raw_range_start: date,
    qfq_range_start: date,
    range_end: date,
    publication_start: date,
    published_at: datetime | None = None,
) -> int:
    publication_time = published_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    with open_database_connection(path) as connection:
        with connection:
            _replace_security_source_range(
                connection,
                staging_source=raw_staging_source,
                published_source=HISTORY_SOURCE,
                range_start=raw_range_start,
                range_end=range_end,
            )
            _replace_security_source_range(
                connection,
                staging_source=qfq_staging_source,
                published_source=qfq_source,
                range_start=qfq_range_start,
                range_end=range_end,
            )
            for symbol in INDEXES:
                _replace_benchmark_source_range(
                    connection,
                    staging_source=index_source(
                        symbol,
                        version_tag=index_version_tag,
                    ),
                    published_source=index_source(symbol),
                    required_end=range_end,
                )
            _replace_benchmark_source_range(
                connection,
                staging_source=index_source(
                    "sh000300",
                    version_tag=index_version_tag,
                ),
                published_source=HISTORY_SOURCE,
                required_end=range_end,
            )
            _replace_historical_market_summaries(
                connection,
                publication_start=publication_start,
                range_end=range_end,
            )
            connection.execute(
                """
                DELETE FROM candle_dataset_publications
                WHERE actual_data_date = ?
                """,
                (range_end.isoformat(),),
            )
            published = _publish_complete_candle_dates(
                connection,
                qfq_source=qfq_source,
                published_at=publication_time,
                publication_start=publication_start,
            )
            target_publication = connection.execute(
                """
                SELECT qfq_source
                FROM candle_dataset_publications
                WHERE actual_data_date = ?
                """,
                (range_end.isoformat(),),
            ).fetchone()
            if target_publication != (qfq_source,):
                raise CandleDataNotFound(
                    "股票与五指数未形成目标日期完整 K 线版本"
                )
            _discard_staged_candle_attempt(
                connection,
                raw_staging_source=raw_staging_source,
                qfq_staging_source=qfq_staging_source,
                index_version_tag=index_version_tag,
            )
            return published


def promote_incremental_candle_date(
    path: Path,
    *,
    raw_staging_source: str,
    qfq_staging_source: str,
    qfq_source: str,
    index_version_tag: str,
    target_date: date,
    published_at: datetime | None = None,
) -> int:
    publication_time = published_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    with open_database_connection(path) as connection:
        with connection:
            _replace_security_source_range(
                connection,
                staging_source=raw_staging_source,
                published_source=HISTORY_SOURCE,
                range_start=target_date,
                range_end=target_date,
            )
            _merge_security_source(
                connection,
                staging_source=qfq_staging_source,
                published_source=qfq_source,
            )
            for symbol in INDEXES:
                _replace_benchmark_source_range(
                    connection,
                    staging_source=index_source(
                        symbol,
                        version_tag=index_version_tag,
                    ),
                    published_source=index_source(symbol),
                    required_end=target_date,
                )
            _replace_benchmark_source_range(
                connection,
                staging_source=index_source(
                    "sh000300",
                    version_tag=index_version_tag,
                ),
                published_source=HISTORY_SOURCE,
                required_end=target_date,
            )
            _replace_historical_market_summaries(
                connection,
                publication_start=target_date,
                range_end=target_date,
            )
            connection.execute(
                """
                DELETE FROM candle_dataset_publications
                WHERE actual_data_date = ?
                """,
                (target_date.isoformat(),),
            )
            published = _publish_complete_candle_dates(
                connection,
                qfq_source=qfq_source,
                published_at=publication_time,
                publication_start=target_date,
            )
            target_publication = connection.execute(
                """
                SELECT qfq_source
                FROM candle_dataset_publications
                WHERE actual_data_date = ?
                """,
                (target_date.isoformat(),),
            ).fetchone()
            if target_publication != (qfq_source,):
                raise CandleDataNotFound(
                    "股票与五指数未形成目标日期完整增量 K 线版本"
                )
            _discard_staged_candle_attempt(
                connection,
                raw_staging_source=raw_staging_source,
                qfq_staging_source=qfq_staging_source,
                index_version_tag=index_version_tag,
            )
            return published


def discard_staged_candle_attempt(
    path: Path,
    *,
    raw_staging_source: str,
    qfq_staging_source: str,
    index_version_tag: str,
) -> None:
    with open_database_connection(path) as connection:
        with connection:
            _discard_staged_candle_attempt(
                connection,
                raw_staging_source=raw_staging_source,
                qfq_staging_source=qfq_staging_source,
                index_version_tag=index_version_tag,
            )


def _discard_staged_candle_attempt(
    connection: sqlite3.Connection,
    *,
    raw_staging_source: str,
    qfq_staging_source: str,
    index_version_tag: str,
) -> None:
    connection.execute(
        "DELETE FROM historical_security_facts WHERE source IN (?, ?)",
        (raw_staging_source, qfq_staging_source),
    )
    connection.execute(
        "DELETE FROM history_ingestion_progress WHERE source IN (?, ?)",
        (raw_staging_source, qfq_staging_source),
    )
    connection.executemany(
        "DELETE FROM historical_benchmark_facts WHERE source = ?",
        [
            (index_source(symbol, version_tag=index_version_tag),)
            for symbol in INDEXES
        ],
    )


def _replace_security_source_range(
    connection: sqlite3.Connection,
    *,
    staging_source: str,
    published_source: str,
    range_start: date,
    range_end: date,
) -> None:
    start = range_start.isoformat()
    end = range_end.isoformat()
    staged_count = cast(
        int,
        connection.execute(
            """
            SELECT COUNT(*) FROM historical_security_facts
            WHERE source = ? AND actual_data_date BETWEEN ? AND ?
            """,
            (staging_source, start, end),
        ).fetchone()[0],
    )
    if staged_count == 0:
        raise CandleDataNotFound("待发布股票日线版本为空")
    connection.execute(
        """
        DELETE FROM historical_security_facts
        WHERE source = ? AND actual_data_date BETWEEN ? AND ?
        """,
        (published_source, start, end),
    )
    connection.execute(
        """
        INSERT INTO historical_security_facts (
            source, actual_data_date, code, name, open, high, low, close,
            previous_close, change_pct, volume, turnover_cny,
            listing_trading_days
        )
        SELECT ?, actual_data_date, code, name, open, high, low, close,
               previous_close, change_pct, volume, turnover_cny,
               listing_trading_days
        FROM historical_security_facts
        WHERE source = ? AND actual_data_date BETWEEN ? AND ?
        """,
        (published_source, staging_source, start, end),
    )
    connection.execute(
        """
        DELETE FROM history_ingestion_progress
        WHERE source = ? AND range_start = ? AND range_end = ?
        """,
        (published_source, start, end),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO history_ingestion_progress (
            source, range_start, range_end, code, row_count, completed_at
        )
        SELECT ?, range_start, range_end, code, row_count, completed_at
        FROM history_ingestion_progress
        WHERE source = ? AND range_start = ? AND range_end = ?
        """,
        (published_source, staging_source, start, end),
    )


def _merge_security_source(
    connection: sqlite3.Connection,
    *,
    staging_source: str,
    published_source: str,
) -> None:
    staged_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM historical_security_facts
            WHERE source = ?
            """,
            (staging_source,),
        ).fetchone()[0]
    )
    if staged_count == 0:
        raise CandleDataNotFound("待合并股票日线版本为空")
    connection.execute(
        """
        INSERT INTO historical_security_facts (
            source, actual_data_date, code, name, open, high, low, close,
            previous_close, change_pct, volume, turnover_cny,
            listing_trading_days
        )
        SELECT ?, actual_data_date, code, name, open, high, low, close,
               previous_close, change_pct, volume, turnover_cny,
               listing_trading_days
        FROM historical_security_facts
        WHERE source = ?
        ON CONFLICT(source, actual_data_date, code) DO UPDATE SET
            name = excluded.name,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            previous_close = excluded.previous_close,
            change_pct = excluded.change_pct,
            volume = excluded.volume,
            turnover_cny = excluded.turnover_cny,
            listing_trading_days = excluded.listing_trading_days
        """,
        (published_source, staging_source),
    )


def _replace_benchmark_source_range(
    connection: sqlite3.Connection,
    *,
    staging_source: str,
    published_source: str,
    required_end: date,
) -> None:
    bounds = connection.execute(
        """
        SELECT MIN(actual_data_date), MAX(actual_data_date)
        FROM historical_benchmark_facts
        WHERE source = ?
        """,
        (staging_source,),
    ).fetchone()
    if bounds is None or bounds[0] is None or bounds[1] is None:
        raise CandleDataNotFound("待发布指数日线版本为空")
    start = cast(str, bounds[0])
    end = cast(str, bounds[1])
    if end != required_end.isoformat():
        raise CandleDataNotFound("指数日线未到目标日期，拒绝发布")
    connection.execute(
        """
        DELETE FROM historical_benchmark_facts
        WHERE source = ? AND actual_data_date BETWEEN ? AND ?
        """,
        (published_source, start, end),
    )
    connection.execute(
        """
        INSERT INTO historical_benchmark_facts (
            source, actual_data_date, name, open, high, low, close, volume
        )
        SELECT ?, actual_data_date, name, open, high, low, close, volume
        FROM historical_benchmark_facts
        WHERE source = ? AND actual_data_date BETWEEN ? AND ?
        """,
        (published_source, staging_source, start, end),
    )


def _replace_historical_market_summaries(
    connection: sqlite3.Connection,
    *,
    publication_start: date,
    range_end: date,
) -> None:
    start = publication_start.isoformat()
    end = range_end.isoformat()
    connection.execute(
        """
        DELETE FROM historical_market_daily_summary
        WHERE source = ? AND actual_data_date BETWEEN ? AND ?
        """,
        (HISTORY_SOURCE, start, end),
    )
    connection.execute(
        """
        INSERT INTO historical_market_daily_summary (
            source, actual_data_date, benchmark_close, turnover_cny,
            security_count, advancers, decliners, unchanged
        )
        SELECT ?, facts.actual_data_date, benchmark.close,
               SUM(facts.turnover_cny), COUNT(*),
               SUM(CASE WHEN facts.change_pct > 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN facts.change_pct < 0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN facts.change_pct = 0 THEN 1 ELSE 0 END)
        FROM historical_security_facts AS facts
        JOIN historical_benchmark_facts AS benchmark
          ON benchmark.source = ?
         AND benchmark.actual_data_date = facts.actual_data_date
        WHERE facts.source = ?
          AND facts.actual_data_date BETWEEN ? AND ?
        GROUP BY facts.actual_data_date, benchmark.close
        """,
        (HISTORY_SOURCE, HISTORY_SOURCE, HISTORY_SOURCE, start, end),
    )


def _publish_complete_candle_dates(
    connection: sqlite3.Connection,
    *,
    qfq_source: str,
    published_at: datetime,
    publication_start: date | None,
) -> int:
    index_dates: list[set[str]] = []
    for symbol in INDEXES:
        rows = connection.execute(
            "SELECT actual_data_date FROM historical_benchmark_facts WHERE source = ?",
            (index_source(symbol),),
        ).fetchall()
        index_dates.append({cast(str, row[0]) for row in rows})
    if any(not dates for dates in index_dates):
        return 0
    candidates = set.intersection(*index_dates)
    published = 0
    for date_text in sorted(candidates):
        if (
            publication_start is not None
            and date_text < publication_start.isoformat()
        ):
            continue
        raw_codes = _codes_for_date(connection, HISTORY_SOURCE, date_text)
        qfq_codes = _codes_for_date(connection, qfq_source, date_text)
        if not raw_codes or raw_codes != qfq_codes:
            continue
        cursor = connection.execute(
            """
            INSERT INTO candle_dataset_publications (
                actual_data_date, qfq_source, published_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(actual_data_date) DO UPDATE SET
                qfq_source = excluded.qfq_source,
                published_at = excluded.published_at
            """,
            (date_text, qfq_source, published_at.isoformat()),
        )
        published += 1 if cursor.rowcount else 0
    return published


def latest_candle_publication(path: Path, requested_date: date) -> date | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT actual_data_date
            FROM candle_dataset_publications
            WHERE actual_data_date <= ?
            ORDER BY actual_data_date DESC
            LIMIT 1
            """,
            (requested_date.isoformat(),),
        ).fetchone()
    return date.fromisoformat(cast(str, row[0])) if row else None


def read_candle_availability(
    path: Path,
    requested_date: date,
) -> CandleAvailability:
    latest = latest_candle_publication(path, requested_date)
    if latest == requested_date:
        return CandleAvailability(
            requested_date=requested_date,
            latest_published_date=latest,
            status="ready",
            message=f"目标日期 {requested_date.isoformat()} 的K线已经完整发布。",
        )
    task = latest_task_run(path, requested_date)
    if (
        task is not None
        and task.status == "running"
        and active_automation_claim_exists(
            path,
            requested_date,
            datetime.now(ZoneInfo("Asia/Shanghai")),
        )
    ):
        return CandleAvailability(
            requested_date=requested_date,
            latest_published_date=latest,
            status="updating",
            message=(
                f"K线正在更新：目标日期 {requested_date.isoformat()}，"
                f"当前完整版本 {latest.isoformat() if latest else '尚无'}。"
            ),
        )
    if latest is None:
        return CandleAvailability(
            requested_date=requested_date,
            latest_published_date=None,
            status="unavailable",
            message=f"目标日期 {requested_date.isoformat()} 前尚无完整K线版本。",
        )
    return CandleAvailability(
        requested_date=requested_date,
        latest_published_date=latest,
        status="stale",
        message=(
            f"K线尚未完整：目标日期 {requested_date.isoformat()}，"
            f"当前完整版本 {latest.isoformat()}。请运行或重试数据更新。"
        ),
    )


def _published_qfq_source(path: Path, actual_date: date) -> str:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT qfq_source FROM candle_dataset_publications
            WHERE actual_data_date = ?
            """,
            (actual_date.isoformat(),),
        ).fetchone()
    if row is None:
        raise CandleDataNotFound("目标日期没有已发布复权版本")
    return cast(str, row[0])


def search_eligible_securities(
    path: Path,
    *,
    query: str,
    requested_date: date,
    limit: int = 20,
) -> list[SecuritySearchResult]:
    del requested_date  # 可浏览范围始终按本机最新完整交易日判断。
    actual_date = latest_candle_publication(path, date.max)
    if actual_date is None:
        return []
    search = query.strip()
    like = f"%{search}%"
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code, name
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
              AND (? = '' OR code LIKE ? OR name LIKE ?)
            ORDER BY
              CASE WHEN code = ? OR name = ? THEN 0 ELSE 1 END,
              code
            LIMIT ?
            """,
            (
                HISTORY_SOURCE,
                actual_date.isoformat(),
                search,
                like,
                like,
                search,
                search,
                max(1, min(limit, 50)),
            ),
        ).fetchall()
    return [
        SecuritySearchResult(
            code=cast(str, row[0]),
            name=cast(str, row[1]),
            exchange="上海" if cast(str, row[0]).startswith("6") else "深圳",
        )
        for row in rows
    ]


def read_candle_series(
    path: Path,
    *,
    instrument_type: Literal["stock", "index"],
    code: str,
    requested_date: date,
    adjustment: Literal["raw", "qfq"] = "qfq",
) -> CandleSeries:
    actual_date = latest_candle_publication(path, requested_date)
    if actual_date is None:
        raise CandleDataNotFound("目标日期前没有完整 K 线数据集")
    if instrument_type == "index":
        if code not in INDEXES:
            raise CandleDataNotFound("不支持该市场指数")
        name = INDEXES[code]
        candles = _read_index_candles(path, code, actual_date)
        effective_adjustment: Literal["raw", "qfq"] = "raw"
        trades: list[TradeMarker] = []
    else:
        eligible = search_eligible_securities(
            path, query=code, requested_date=actual_date, limit=50
        )
        exact = next((item for item in eligible if item.code == code), None)
        if exact is None:
            raise CandleDataNotFound("该股票当前不在可浏览范围内")
        name = exact.name
        candles = _read_stock_candles(path, code, actual_date, adjustment)
        effective_adjustment = adjustment
        trades = _read_real_trade_markers(path, code, actual_date)
    if not candles:
        raise CandleDataNotFound("该标的没有可用日 K 数据")
    values = _with_rsi(candles)
    return CandleSeries(
        instrument_type=instrument_type,
        code=code,
        name=name,
        adjustment=effective_adjustment,
        requested_date=requested_date,
        actual_data_date=actual_date,
        coverage_start=values[0].date,
        coverage_end=values[-1].date,
        candles=values,
        trades=trades,
    )


def _read_stock_candles(
    path: Path,
    code: str,
    actual_date: date,
    adjustment: Literal["raw", "qfq"],
) -> list[CandlePoint]:
    source = (
        _published_qfq_source(path, actual_date)
        if adjustment == "qfq"
        else HISTORY_SOURCE
    )
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, open, high, low, close, volume,
                   turnover_cny, change_pct
            FROM historical_security_facts
            WHERE source = ? AND code = ? AND actual_data_date <= ?
            ORDER BY actual_data_date
            """,
            (source, code, actual_date.isoformat()),
        ).fetchall()
    return [_candle_from_row(row) for row in rows]


def _read_index_candles(
    path: Path, symbol: str, actual_date: date
) -> list[CandlePoint]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, open, high, low, close, volume
            FROM historical_benchmark_facts
            WHERE source = ? AND actual_data_date <= ?
            ORDER BY actual_data_date
            """,
            (index_source(symbol), actual_date.isoformat()),
        ).fetchall()
    closes = [float(row[4]) for row in rows]
    result: list[CandlePoint] = []
    for index, row in enumerate(rows):
        previous = closes[index - 1] if index else closes[index]
        change_pct = (closes[index] / previous - 1) * 100 if previous else 0.0
        result.append(
            CandlePoint(
                date=date.fromisoformat(cast(str, row[0])),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=closes[index],
                volume=int(row[5]),
                turnover_cny=0,
                change_pct=change_pct,
                rsi14=None,
            )
        )
    return result


def _candle_from_row(row: tuple[object, ...]) -> CandlePoint:
    return CandlePoint(
        date=date.fromisoformat(cast(str, row[0])),
        open=_number(row[1]),
        high=_number(row[2]),
        low=_number(row[3]),
        close=_number(row[4]),
        volume=_integer(row[5]),
        turnover_cny=_integer(row[6]),
        change_pct=float(str(row[7])),
        rsi14=None,
    )


def _with_rsi(candles: list[CandlePoint], period: int = 14) -> list[CandlePoint]:
    if len(candles) <= period:
        return candles
    gains: list[float] = []
    losses: list[float] = []
    rsi_values: list[float | None] = [None] * len(candles)
    for index in range(1, len(candles)):
        movement = candles[index].close - candles[index - 1].close
        gains.append(max(movement, 0.0))
        losses.append(max(-movement, 0.0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    rsi_values[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(candles)):
        average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        rsi_values[index] = _rsi_value(average_gain, average_loss)
    return [
        candle.model_copy(update={"rsi14": rsi_values[index]})
        for index, candle in enumerate(candles)
    ]


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + average_gain / average_loss), 4)


def _read_real_trade_markers(
    path: Path, code: str, actual_date: date
) -> list[TradeMarker]:
    markers: list[TradeMarker] = []
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM daily_snapshots
            WHERE target_date <= ?
            ORDER BY target_date
            """,
            (actual_date.isoformat(),),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(cast(str, row[0]))
            portfolio = payload.get("portfolio_review", {})
            if portfolio.get("is_demo", True):
                continue
            for trade in portfolio.get("trades", []):
                if trade.get("security", {}).get("code") != code:
                    continue
                price = float(trade["execution_price"])
                quantity = int(trade["quantity"])
                side = str(trade["side"])
                if side not in ("buy", "sell"):
                    continue
                markers.append(
                    TradeMarker(
                        date=date.fromisoformat(str(trade["date"])),
                        side=cast(Literal["buy", "sell"], side),
                        price=price,
                        amount_cny=price * quantity,
                    )
                )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return markers


def _codes_for_date(
    connection: sqlite3.Connection, source: str, date_text: str
) -> set[str]:
    rows = connection.execute(
        """
        SELECT code FROM historical_security_facts
        WHERE source = ? AND actual_data_date = ?
        """,
        (source, date_text),
    ).fetchall()
    return {cast(str, row[0]) for row in rows}


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)


def _date_value(value: object) -> date | None:
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _number(value: object) -> float:
    numeric = pd.to_numeric(str(value), errors="coerce")
    if pd.isna(numeric):
        raise CandleDataNotFound("指数包含非法价格")
    result = float(numeric)
    if result <= 0:
        raise CandleDataNotFound("指数包含非正价格")
    return result


def _integer(value: object) -> int:
    numeric = pd.to_numeric(str(value), errors="coerce")
    return 0 if pd.isna(numeric) else max(0, round(float(numeric)))


def factor_content_hash(frame: pd.DataFrame) -> str:
    """为后续复权版本去重提供稳定内容标识。"""
    payload = frame.sort_values("date").to_json(orient="records", date_format="iso")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
