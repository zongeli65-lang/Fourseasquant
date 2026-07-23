from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fourseasquant.akshare_history import (
    HISTORY_QFQ_SOURCE,
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


def refresh_one_year_candles(
    path: Path,
    *,
    requested_end_date: date,
    max_workers: int = 2,
    progress_callback: ProgressCallback | None = None,
    warmup_trading_days: int = 60,
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
    if raw.failed_codes or raw.completed_symbols != raw.total_symbols:
        raise CandleRefreshError(
            f"不复权股票日线未完整：失败 {len(raw.failed_codes)} 只"
        )
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
    if adjusted.failed_codes or adjusted.completed_symbols != adjusted.total_symbols:
        raise CandleRefreshError(
            f"前复权股票日线未完整：失败 {len(adjusted.failed_codes)} 只"
        )
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
