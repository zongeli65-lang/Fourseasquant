from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.akshare_history import (
    HISTORY_QFQ_SOURCE,
    HISTORY_SOURCE,
    HistoryImportSummary,
    ProgressCallback,
    build_akshare_one_year_history_importer,
    build_akshare_qfq_history_importer,
)
from fourseasquant.candlesticks import (
    import_index_candles,
    latest_candle_publication,
    publish_complete_candle_dates,
)
from fourseasquant.settings import read_settings


@dataclass(frozen=True)
class CandleRefreshSummary:
    actual_data_date: date
    symbol_count: int
    published_days: int


class CandleRefreshError(RuntimeError):
    pass


SecurityDailyFactSnapshot = Callable[[], pd.DataFrame]


def refresh_one_year_candles(
    path: Path,
    *,
    requested_end_date: date,
    max_workers: int = 2,
    progress_callback: ProgressCallback | None = None,
    warmup_trading_days: int = 60,
    security_daily_fact_snapshot: SecurityDailyFactSnapshot | None = None,
) -> CandleRefreshSummary:
    import akshare as ak  # type: ignore[import-untyped]

    settings = read_settings(path)
    raw = build_akshare_one_year_history_importer(
        max_workers=max_workers,
        progress_callback=progress_callback,
        include_technical_boards=True,
    ).import_one_year(
        path=path,
        requested_end_date=requested_end_date,
        new_stock_exclusion_days=settings.new_stock_exclusion_days,
        warmup_trading_days=warmup_trading_days,
    )
    try:
        _ensure_import_ready(raw, requested_end_date, label="不复权股票日线")
    except CandleRefreshError:
        _revoke_candle_publication(path, requested_end_date)
        raise
    version_tag = requested_end_date.isoformat()
    qfq_source = f"{HISTORY_QFQ_SOURCE}:{version_tag}"
    adjusted = build_akshare_qfq_history_importer(
        max_workers=max_workers,
        progress_callback=progress_callback,
        version_tag=version_tag,
        include_technical_boards=True,
    ).import_one_year(
        path=path,
        requested_end_date=requested_end_date,
        new_stock_exclusion_days=settings.new_stock_exclusion_days,
        warmup_trading_days=warmup_trading_days,
    )
    try:
        _ensure_import_ready(adjusted, requested_end_date, label="前复权股票日线")
    except CandleRefreshError:
        _revoke_candle_publication(path, requested_end_date)
        raise
    if security_daily_fact_snapshot is not None:
        coverage_snapshot = security_daily_fact_snapshot()
    elif requested_end_date == datetime.now(ZoneInfo("Asia/Shanghai")).date():
        coverage_snapshot = ak.stock_zh_a_spot()
    else:
        _revoke_candle_publication(path, requested_end_date)
        raise CandleRefreshError("历史目标日缺少个股日频事实覆盖证据")
    _validate_security_daily_fact_coverage(
        path,
        requested_end_date=requested_end_date,
        raw_range_start=raw.range_start,
        qfq_range_start=adjusted.range_start,
        qfq_source=qfq_source,
        snapshot=coverage_snapshot,
    )
    _revoke_candle_publication(path, requested_end_date)
    import_index_candles(
        path,
        requested_end_date=requested_end_date,
        index_history=lambda symbol: ak.stock_zh_index_daily(symbol=symbol),
        warmup_trading_days=warmup_trading_days,
    )
    official_start = _one_year_start(adjusted.range_end)
    published_days = publish_complete_candle_dates(
        path,
        qfq_source=qfq_source,
        publication_start=official_start,
    )
    latest = latest_candle_publication(path, requested_end_date)
    if latest != adjusted.range_end:
        raise CandleRefreshError("股票与五指数未形成同日完整 K 线版本")
    return CandleRefreshSummary(
        actual_data_date=latest,
        symbol_count=adjusted.completed_symbols,
        published_days=published_days,
    )


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)


def _ensure_import_ready(
    summary: HistoryImportSummary,
    requested_end_date: date,
    *,
    label: str,
) -> None:
    if summary.failed_codes or summary.completed_symbols != summary.total_symbols:
        raise CandleRefreshError(
            f"{label}未完整：失败 {len(summary.failed_codes)} 只"
        )
    if summary.range_end != requested_end_date:
        raise CandleRefreshError(
            "目标交易日行情尚未就绪："
            f"请求 {requested_end_date.isoformat()}，"
            f"{label}仅到 {summary.range_end.isoformat()}"
        )


def _validate_security_daily_fact_coverage(
    path: Path,
    *,
    requested_end_date: date,
    raw_range_start: date,
    qfq_range_start: date,
    qfq_source: str,
    snapshot: pd.DataFrame,
) -> None:
    if snapshot.empty or "代码" not in snapshot.columns or "成交量" not in snapshot.columns:
        raise CandleRefreshError("个股日频事实覆盖校验数据不可用")
    frame = snapshot.copy()
    frame["代码"] = (
        frame["代码"].astype(str).str.strip().str.lower().str.removeprefix("sh")
        .str.removeprefix("sz").str.zfill(6)
    )
    frame["成交量"] = pd.to_numeric(frame["成交量"], errors="coerce").fillna(0)
    if "名称" in frame.columns:
        names = frame["名称"].astype(str).str.upper()
        frame = frame[~names.str.contains("ST", na=False)]
        frame = frame[~frame["名称"].astype(str).str.contains("退", na=False)]
    active_codes = set(frame.loc[frame["成交量"] > 0, "代码"].tolist())
    target = requested_end_date.isoformat()
    with sqlite3.connect(path) as connection:
        previously_eligible = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT code
                FROM historical_security_facts
                WHERE source = ? AND actual_data_date < ?
                """,
                (HISTORY_SOURCE, target),
            )
        }
        raw_codes = _codes_for_source_date(connection, HISTORY_SOURCE, target)
        qfq_codes = _codes_for_source_date(connection, qfq_source, target)
    expected_codes = active_codes & previously_eligible
    if not expected_codes:
        raise CandleRefreshError("个股日频事实覆盖校验没有形成有效样本")
    missing_raw = expected_codes - raw_codes
    missing_qfq = expected_codes - qfq_codes
    if missing_raw or missing_qfq:
        _invalidate_incomplete_symbols(
            path,
            source=HISTORY_SOURCE,
            range_start=raw_range_start,
            range_end=requested_end_date,
            codes=missing_raw,
        )
        _invalidate_incomplete_symbols(
            path,
            source=qfq_source,
            range_start=qfq_range_start,
            range_end=requested_end_date,
            codes=missing_qfq,
        )
        missing_codes = "、".join(sorted(missing_raw | missing_qfq)[:10])
        _revoke_candle_publication(path, requested_end_date)
        raise CandleRefreshError(f"目标交易日个股日频事实缺失：{missing_codes}")


def _codes_for_source_date(
    connection: sqlite3.Connection,
    source: str,
    target: str,
) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT code FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            """,
            (source, target),
        )
    }


def _invalidate_incomplete_symbols(
    path: Path,
    *,
    source: str,
    range_start: date,
    range_end: date,
    codes: set[str],
) -> None:
    if not codes:
        return
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            DELETE FROM history_ingestion_progress
            WHERE source = ? AND range_start = ? AND range_end = ? AND code = ?
            """,
            [
                (
                    source,
                    range_start.isoformat(),
                    range_end.isoformat(),
                    code,
                )
                for code in sorted(codes)
            ],
        )


def _revoke_candle_publication(path: Path, target_date: date) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            DELETE FROM candle_dataset_publications
            WHERE actual_data_date = ?
            """,
            (target_date.isoformat(),),
        )
