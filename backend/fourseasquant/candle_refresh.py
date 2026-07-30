from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.akshare_history import (
    AkshareOneYearHistoryImporter,
    HISTORY_QFQ_SOURCE,
    HISTORY_SOURCE,
    HistoryImportSummary,
    ProgressCallback,
    SecurityListing,
    build_akshare_one_year_history_importer,
    build_akshare_qfq_history_importer,
)
from fourseasquant.database import HistoricalSecurityFactRow
from fourseasquant.candlesticks import (
    discard_staged_candle_attempt,
    import_index_candles,
    latest_candle_publication,
    promote_incremental_candle_date,
    promote_staged_candle_dates,
)
from fourseasquant.settings import read_settings
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.incremental_market import (
    QfqFactor,
    build_incremental_market_batch,
)


@dataclass(frozen=True)
class CandleRefreshSummary:
    actual_data_date: date
    symbol_count: int
    published_days: int
    qfq_source: str


class CandleRefreshError(RuntimeError):
    pass


class AkshareIncrementalClient(Protocol):
    def stock_zh_index_daily(self, *, symbol: str) -> pd.DataFrame: ...

    def stock_zh_a_spot(self) -> pd.DataFrame: ...


SecurityDailyFactSnapshot = Callable[[], pd.DataFrame]
INCREMENTAL_MIN_WORKERS = 8


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
    version_tag = requested_end_date.isoformat()
    qfq_source = f"{HISTORY_QFQ_SOURCE}:{version_tag}"
    attempt_version_tag = f"{version_tag}:attempt-{uuid4().hex}"
    attempt_raw_source = f"{HISTORY_SOURCE}:{attempt_version_tag}"
    attempt_qfq_source = f"{HISTORY_QFQ_SOURCE}:{attempt_version_tag}"
    try:
        incremental_base = _incremental_base(
            path,
            requested_end_date=requested_end_date,
        )
        resolved_workers = (
            max(max_workers, INCREMENTAL_MIN_WORKERS)
            if incremental_base is not None
            else max_workers
        )
        raw_importer = build_akshare_one_year_history_importer(
            max_workers=resolved_workers,
            progress_callback=progress_callback,
            include_technical_boards=True,
            version_tag=attempt_version_tag,
        )
        qfq_importer = build_akshare_qfq_history_importer(
            max_workers=resolved_workers,
            progress_callback=progress_callback,
            version_tag=attempt_version_tag,
            include_technical_boards=True,
        )
        if incremental_base is None:
            raw = raw_importer.import_one_year(
                path=path,
                requested_end_date=requested_end_date,
                new_stock_exclusion_days=settings.new_stock_exclusion_days,
                warmup_trading_days=warmup_trading_days,
            )
        else:
            previous_date, previous_qfq_source, qfq_history_start = (
                incremental_base
            )
            qfq_source = previous_qfq_source
            raw, adjusted, changed_qfq_codes, coverage_snapshot = (
                _refresh_incremental_snapshot(
                    path,
                    ak=ak,
                    raw_importer=raw_importer,
                    requested_end_date=requested_end_date,
                    previous_date=previous_date,
                    raw_staging_source=attempt_raw_source,
                    qfq_staging_source=attempt_qfq_source,
                    new_stock_exclusion_days=settings.new_stock_exclusion_days,
                )
            )
        _ensure_import_ready(raw, requested_end_date, label="不复权股票日线")
        if incremental_base is None:
            adjusted = qfq_importer.import_one_year(
                path=path,
                requested_end_date=requested_end_date,
                new_stock_exclusion_days=settings.new_stock_exclusion_days,
                warmup_trading_days=warmup_trading_days,
            )
            qfq_publish_start = adjusted.range_start
        else:
            if changed_qfq_codes:
                repaired = qfq_importer.import_incremental(
                    path=path,
                    requested_start_date=qfq_history_start,
                    requested_end_date=requested_end_date,
                    new_stock_exclusion_days=settings.new_stock_exclusion_days,
                    included_codes=changed_qfq_codes,
                )
                _ensure_import_ready(
                    repaired,
                    requested_end_date,
                    label="除权变化股票前复权日线",
                )
            qfq_publish_start = requested_end_date
        _ensure_import_ready(adjusted, requested_end_date, label="前复权股票日线")
        if security_daily_fact_snapshot is not None:
            coverage_snapshot = security_daily_fact_snapshot()
        elif (
            incremental_base is None
            and requested_end_date
            == datetime.now(ZoneInfo("Asia/Shanghai")).date()
        ):
            coverage_snapshot = ak.stock_zh_a_spot()
        elif incremental_base is None:
            raise CandleRefreshError("历史目标日缺少个股日频事实覆盖证据")
        _validate_security_daily_fact_coverage(
            path,
            requested_end_date=requested_end_date,
            raw_range_start=raw.range_start,
            qfq_range_start=qfq_publish_start,
            raw_source=attempt_raw_source,
            qfq_source=attempt_qfq_source,
            new_stock_exclusion_days=settings.new_stock_exclusion_days,
            listing_dates=raw.listing_dates,
            snapshot=coverage_snapshot,
        )
        import_index_candles(
            path,
            requested_end_date=requested_end_date,
            index_history=lambda symbol: ak.stock_zh_index_daily(symbol=symbol),
            warmup_trading_days=warmup_trading_days,
            version_tag=attempt_version_tag,
        )
        official_start = _one_year_start(adjusted.range_end)
        if incremental_base is None:
            published_days = promote_staged_candle_dates(
                path,
                raw_staging_source=attempt_raw_source,
                qfq_staging_source=attempt_qfq_source,
                qfq_source=qfq_source,
                index_version_tag=attempt_version_tag,
                raw_range_start=raw.range_start,
                qfq_range_start=qfq_publish_start,
                range_end=adjusted.range_end,
                publication_start=official_start,
            )
        else:
            published_days = promote_incremental_candle_date(
                path,
                raw_staging_source=attempt_raw_source,
                qfq_staging_source=attempt_qfq_source,
                qfq_source=qfq_source,
                index_version_tag=attempt_version_tag,
                target_date=adjusted.range_end,
            )
        latest = latest_candle_publication(path, requested_end_date)
        if latest != adjusted.range_end:
            raise CandleRefreshError("股票与五指数未形成同日完整 K 线版本")
        return CandleRefreshSummary(
            actual_data_date=latest,
            symbol_count=adjusted.completed_symbols,
            published_days=published_days,
            qfq_source=qfq_source,
        )
    finally:
        try:
            discard_staged_candle_attempt(
                path,
                raw_staging_source=attempt_raw_source,
                qfq_staging_source=attempt_qfq_source,
                index_version_tag=attempt_version_tag,
            )
        except sqlite3.Error:
            # 尝试数据从不被读取；清理失败不能覆盖原始刷新错误。
            pass


def _incremental_base(
    path: Path,
    *,
    requested_end_date: date,
) -> tuple[date, str, date] | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT publication.actual_data_date, publication.qfq_source,
                   MIN(facts.actual_data_date)
            FROM candle_dataset_publications AS publication
            JOIN historical_security_facts AS facts
              ON facts.source = publication.qfq_source
            WHERE publication.actual_data_date < ?
            GROUP BY publication.actual_data_date, publication.qfq_source
            ORDER BY publication.actual_data_date DESC
            LIMIT 1
            """,
            (requested_end_date.isoformat(),),
        ).fetchone()
    if row is None or row[2] is None:
        return None
    return (
        date.fromisoformat(str(row[0])),
        str(row[1]),
        date.fromisoformat(str(row[2])),
    )


def _refresh_incremental_snapshot(
    path: Path,
    *,
    ak: AkshareIncrementalClient,
    raw_importer: AkshareOneYearHistoryImporter,
    requested_end_date: date,
    previous_date: date,
    raw_staging_source: str,
    qfq_staging_source: str,
    new_stock_exclusion_days: int,
) -> tuple[
    HistoryImportSummary,
    HistoryImportSummary,
    set[str],
    pd.DataFrame,
]:
    benchmark = ak.stock_zh_index_daily(symbol="sh000300").copy()
    benchmark["date"] = pd.to_datetime(
        benchmark["date"],
        errors="coerce",
    ).dt.date
    trading_dates = sorted(
        item
        for item in benchmark["date"].dropna().tolist()
        if isinstance(item, date) and item <= requested_end_date
    )
    if not trading_dates or trading_dates[-1] != requested_end_date:
        latest = trading_dates[-1].isoformat() if trading_dates else "无"
        raise CandleRefreshError(
            "目标交易日行情尚未就绪："
            f"请求 {requested_end_date.isoformat()}，"
            f"沪深 300 仅到 {latest}"
        )

    listings = raw_importer.eligible_listings(requested_end_date)
    snapshot = ak.stock_zh_a_spot().copy()
    active_symbols = _active_snapshot_symbols(
        snapshot,
        listings,
        trading_dates=trading_dates,
        new_stock_exclusion_days=new_stock_exclusion_days,
    )
    factors = {
        code: QfqFactor(
            effective_date=requested_end_date,
            value=1.0,
        )
        for code in active_symbols
    }

    batch = build_incremental_market_batch(
        snapshot,
        target_date=requested_end_date,
        listings=listings,
        trading_dates=trading_dates,
        qfq_factors=factors,
        new_stock_exclusion_days=new_stock_exclusion_days,
    )
    if batch.missing_factor_codes:
        sample = "、".join(sorted(batch.missing_factor_codes)[:10])
        raise CandleRefreshError(
            "目标交易日缺少前复权因子："
            f"{sample}"
        )
    if not batch.raw_facts or len(batch.raw_facts) != len(batch.qfq_facts):
        raise CandleRefreshError("全市场单日快照没有形成完整股票日线")
    _save_incremental_fact_batches(
        path,
        raw_source=raw_staging_source,
        qfq_source=qfq_staging_source,
        target_date=requested_end_date,
        raw_facts=batch.raw_facts,
        qfq_facts=batch.qfq_facts,
    )

    changed_qfq_codes = _potential_factor_change_codes(
        path,
        snapshot=snapshot,
        previous_date=previous_date,
        codes=set(active_symbols),
    )
    listing_dates = {
        listing.code: listing.listing_date
        for listing in listings
    }
    summary = HistoryImportSummary(
        range_start=requested_end_date,
        range_end=requested_end_date,
        total_symbols=len(batch.raw_facts),
        completed_symbols=len(batch.raw_facts),
        failed_codes=[],
        published_days=0,
        listing_dates=listing_dates,
    )
    adjusted = HistoryImportSummary(
        range_start=requested_end_date,
        range_end=requested_end_date,
        total_symbols=len(batch.qfq_facts),
        completed_symbols=len(batch.qfq_facts),
        failed_codes=[],
        published_days=0,
        listing_dates=listing_dates,
    )
    return summary, adjusted, changed_qfq_codes, snapshot


def _active_snapshot_symbols(
    snapshot: pd.DataFrame,
    listings: list[SecurityListing],
    *,
    trading_dates: list[date],
    new_stock_exclusion_days: int,
) -> dict[str, str]:
    listing_symbols = {
        listing.code: listing.symbol
        for listing in listings
        if sum(
            listing.listing_date <= trading_date
            for trading_date in trading_dates
        )
        >= new_stock_exclusion_days
    }
    if snapshot.empty:
        return {}
    frame = snapshot.copy()
    frame["_code"] = frame["代码"].map(_normalise_snapshot_code)
    frame["_volume"] = pd.to_numeric(
        frame["成交量"],
        errors="coerce",
    ).fillna(0)
    names = frame["名称"].astype(str)
    frame = frame[
        (frame["_volume"] > 0)
        & ~names.str.upper().str.contains("ST", na=False)
        & ~names.str.contains("退", na=False)
    ]
    return {
        code: listing_symbols[code]
        for code in frame["_code"].tolist()
        if code in listing_symbols
    }


def _normalise_snapshot_code(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text.zfill(6) if text.isdigit() else text


def _potential_factor_change_codes(
    path: Path,
    *,
    snapshot: pd.DataFrame,
    previous_date: date,
    codes: set[str],
) -> set[str]:
    if not codes or snapshot.empty:
        return set()
    with open_database_connection(path) as connection:
        previous_rows = connection.execute(
            """
            SELECT code, close
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            """,
            (HISTORY_SOURCE, previous_date.isoformat()),
        ).fetchall()
    previous_close = {
        str(row[0]): float(row[1])
        for row in previous_rows
        if str(row[0]) in codes
    }
    changed: set[str] = set()
    for row in snapshot.to_dict("records"):
        code = _normalise_snapshot_code(row.get("代码"))
        if code not in codes or code not in previous_close:
            continue
        settlement = pd.to_numeric(
            str(row.get("昨收")),
            errors="coerce",
        )
        if pd.isna(settlement):
            continue
        if abs(float(settlement) - previous_close[code]) > 0.011:
            changed.add(code)
    return changed


def _save_incremental_fact_batches(
    path: Path,
    *,
    raw_source: str,
    qfq_source: str,
    target_date: date,
    raw_facts: list[HistoricalSecurityFactRow],
    qfq_facts: list[HistoricalSecurityFactRow],
) -> None:
    completed_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    with open_database_connection(path) as connection:
        with connection:
            for source, facts in (
                (raw_source, raw_facts),
                (qfq_source, qfq_facts),
            ):
                connection.executemany(
                    """
                    INSERT INTO historical_security_facts (
                        source, actual_data_date, code, name, open, high, low,
                        close, previous_close, change_pct, volume,
                        turnover_cny, listing_trading_days
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            source,
                            fact.actual_data_date.isoformat(),
                            fact.code,
                            fact.name,
                            fact.open,
                            fact.high,
                            fact.low,
                            fact.close,
                            fact.previous_close,
                            fact.change_pct,
                            fact.volume,
                            fact.turnover_cny,
                            fact.listing_trading_days,
                        )
                        for fact in facts
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO history_ingestion_progress (
                        source, range_start, range_end, code, row_count,
                        completed_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    [
                        (
                            source,
                            target_date.isoformat(),
                            target_date.isoformat(),
                            fact.code,
                            completed_at,
                        )
                        for fact in facts
                    ],
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
    raw_source: str = HISTORY_SOURCE,
    qfq_source: str,
    new_stock_exclusion_days: int,
    listing_dates: dict[str, date],
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
    with open_database_connection(path) as connection:
        previously_eligible = _eligible_codes(
            connection,
            target=target,
            raw_source=raw_source,
            new_stock_exclusion_days=new_stock_exclusion_days,
            listing_dates=listing_dates,
        )
        raw_codes = _codes_for_source_date(connection, raw_source, target)
        qfq_codes = _codes_for_source_date(connection, qfq_source, target)
    expected_codes = active_codes & previously_eligible
    if not expected_codes:
        raise CandleRefreshError("个股日频事实覆盖校验没有形成有效样本")
    missing_raw = expected_codes - raw_codes
    missing_qfq = expected_codes - qfq_codes
    if missing_raw or missing_qfq:
        _invalidate_incomplete_target(
            path,
            target_date=requested_end_date,
            raw_range_start=raw_range_start,
            qfq_range_start=qfq_range_start,
            raw_source=raw_source,
            qfq_source=qfq_source,
            missing_raw=missing_raw,
            missing_qfq=missing_qfq,
        )
        missing_codes = "、".join(sorted(missing_raw | missing_qfq)[:10])
        raise CandleRefreshError(f"目标交易日个股日频事实缺失：{missing_codes}")


def _eligible_codes(
    connection: sqlite3.Connection,
    *,
    target: str,
    raw_source: str,
    new_stock_exclusion_days: int,
    listing_dates: dict[str, date],
) -> set[str]:
    benchmark_dates = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT actual_data_date
            FROM historical_benchmark_facts
            WHERE source = ? AND actual_data_date <= ?
            """,
            (HISTORY_SOURCE, target),
        )
    }
    benchmark_dates.update(
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT actual_data_date
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date <= ?
            """,
            (raw_source, target),
        )
    )
    eligible: set[str] = set()
    eligible.update(
        code
        for code, listing_date in listing_dates.items()
        if sum(
            listing_date.isoformat() <= trading_date <= target
            for trading_date in benchmark_dates
        )
        >= new_stock_exclusion_days
    )
    for code, last_date, listing_days in connection.execute(
        """
        SELECT code, MAX(actual_data_date), MAX(listing_trading_days)
        FROM historical_security_facts
        WHERE source = ? AND actual_data_date < ?
        GROUP BY code
        """,
        (HISTORY_SOURCE, target),
    ):
        projected_listing_days = int(listing_days) + sum(
            str(last_date) < trading_date <= target
            for trading_date in benchmark_dates
        )
        if projected_listing_days >= new_stock_exclusion_days:
            eligible.add(str(code))
    return eligible


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


def _invalidate_incomplete_target(
    path: Path,
    *,
    target_date: date,
    raw_range_start: date,
    qfq_range_start: date,
    raw_source: str,
    qfq_source: str,
    missing_raw: set[str],
    missing_qfq: set[str],
) -> None:
    with open_database_connection(path) as connection:
        for source, range_start, codes in (
            (raw_source, raw_range_start, missing_raw),
            (qfq_source, qfq_range_start, missing_qfq),
        ):
            connection.executemany(
                """
                DELETE FROM history_ingestion_progress
                WHERE source = ? AND range_start = ? AND range_end = ? AND code = ?
                """,
                [
                    (
                        source,
                        range_start.isoformat(),
                        target_date.isoformat(),
                        code,
                    )
                    for code in sorted(codes)
                ],
            )
