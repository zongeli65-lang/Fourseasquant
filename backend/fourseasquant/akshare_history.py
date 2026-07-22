from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.akshare_market_data import (
    BenchmarkDailyFact,
    DailyMarketFacts,
    SecurityDailyFact,
)
from fourseasquant.database import (
    HistoricalBenchmarkFactRow,
    HistoricalMarketSummaryRow,
    HistoricalSecurityFactRow,
    completed_history_symbols,
    historical_security_facts_for_date,
    save_history_symbol_batch,
    save_historical_benchmark_fact,
    save_historical_market_summary,
    save_market_facts,
)


FrameFactory = Callable[[], pd.DataFrame]
StockHistoryFactory = Callable[[str, date, date], pd.DataFrame]
ProgressCallback = Callable[[int, int, str, bool], None]
HISTORY_SOURCE = "akshare_sina_daily"


@dataclass(frozen=True)
class SecurityListing:
    code: str
    symbol: str
    name: str
    listing_date: date


@dataclass(frozen=True)
class HistoryImportSummary:
    range_start: date
    range_end: date
    total_symbols: int
    completed_symbols: int
    failed_codes: list[str]
    published_days: int


class HistoryDataQualityError(RuntimeError):
    """历史缓存完整但无法形成完整交易日结果。"""


class AkshareOneYearHistoryImporter:
    def __init__(
        self,
        *,
        sh_listing: FrameFactory,
        sz_listing: FrameFactory,
        index_history: FrameFactory,
        stock_history: StockHistoryFactory,
        max_workers: int = 2,
        max_attempts: int = 3,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._sh_listing = sh_listing
        self._sz_listing = sz_listing
        self._index_history = index_history
        self._stock_history = stock_history
        self._max_workers = max(1, max_workers)
        self._max_attempts = max(1, max_attempts)
        self._progress_callback = progress_callback

    def import_one_year(
        self,
        *,
        path: Path,
        requested_end_date: date,
        new_stock_exclusion_days: int,
        collected_at: datetime | None = None,
    ) -> HistoryImportSummary:
        imported_at = collected_at or datetime.now(ZoneInfo("Asia/Shanghai"))
        benchmark_by_date = self._benchmark_rows(requested_end_date)
        range_end = max(benchmark_by_date)
        range_start = _one_year_start(range_end)
        trading_dates = sorted(
            trading_date
            for trading_date in benchmark_by_date
            if range_start <= trading_date <= range_end
        )
        all_trading_dates = sorted(benchmark_by_date)
        listings = self._main_board_listings(range_end)
        completed = completed_history_symbols(
            path,
            source=HISTORY_SOURCE,
            range_start=range_start,
            range_end=range_end,
        )
        pending = [listing for listing in listings if listing.code not in completed]
        failed_codes: list[str] = []
        newly_completed = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(
                    self._load_symbol_facts_with_retry,
                    listing,
                    range_start,
                    range_end,
                    all_trading_dates,
                    new_stock_exclusion_days,
                ): listing
                for listing in pending
            }
            for future in as_completed(futures):
                listing = futures[future]
                try:
                    facts = future.result()
                except Exception:
                    failed_codes.append(listing.code)
                    newly_completed += 1
                    self._report_progress(
                        len(completed) + newly_completed,
                        len(listings),
                        listing.code,
                        False,
                    )
                    continue
                save_history_symbol_batch(
                    path,
                    source=HISTORY_SOURCE,
                    range_start=range_start,
                    range_end=range_end,
                    code=listing.code,
                    facts=facts,
                    completed_at=imported_at,
                )
                newly_completed += 1
                self._report_progress(
                    len(completed) + newly_completed,
                    len(listings),
                    listing.code,
                    True,
                )

        completed_after = completed_history_symbols(
            path,
            source=HISTORY_SOURCE,
            range_start=range_start,
            range_end=range_end,
        )
        published_days = 0
        if len(completed_after) == len(listings):
            published_days = self._publish_complete_days(
                path=path,
                trading_dates=trading_dates,
                benchmark_by_date=benchmark_by_date,
                collected_at=imported_at,
            )

        return HistoryImportSummary(
            range_start=range_start,
            range_end=range_end,
            total_symbols=len(listings),
            completed_symbols=len(completed_after),
            failed_codes=sorted(failed_codes),
            published_days=published_days,
        )

    def _report_progress(
        self, completed: int, total: int, code: str, succeeded: bool
    ) -> None:
        if self._progress_callback is not None:
            self._progress_callback(completed, total, code, succeeded)

    def _load_symbol_facts_with_retry(
        self,
        listing: SecurityListing,
        range_start: date,
        range_end: date,
        trading_dates: list[date],
        new_stock_exclusion_days: int,
    ) -> list[HistoricalSecurityFactRow]:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._load_symbol_facts(
                    listing,
                    range_start,
                    range_end,
                    trading_dates,
                    new_stock_exclusion_days,
                )
            except Exception as error:
                last_error = error
                if attempt < self._max_attempts:
                    sleep(float(attempt))
        if last_error is None:
            raise RuntimeError("历史行情读取失败")
        raise last_error

    def _benchmark_rows(self, requested_end_date: date) -> dict[date, dict[str, object]]:
        frame = self._index_history().copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame[frame["date"] <= requested_end_date]
        rows: dict[date, dict[str, object]] = {}
        for row in frame.to_dict("records"):
            trading_date = _date_value(row.get("date"))
            if trading_date is not None:
                rows[trading_date] = {
                    str(key): value for key, value in row.items()
                }
        if not rows:
            raise HistoryDataQualityError("沪深 300 没有可用历史交易日")
        return rows

    def _main_board_listings(self, range_end: date) -> list[SecurityListing]:
        listings: list[SecurityListing] = []
        for row in self._sh_listing().to_dict("records"):
            code = _normalise_code(row.get("证券代码"))
            name = str(row.get("证券简称", "")).strip()
            listing_date = _date_value(row.get("上市日期"))
            if (
                code
                and listing_date is not None
                and listing_date <= range_end
                and not _is_excluded_name(name)
            ):
                listings.append(
                    SecurityListing(code, f"sh{code}", name, listing_date)
                )

        sz_listing = self._sz_listing()
        if "板块" in sz_listing.columns:
            sz_listing = sz_listing[sz_listing["板块"] == "主板"]
        for row in sz_listing.to_dict("records"):
            code = _normalise_code(row.get("A股代码"))
            name = str(row.get("A股简称", "")).strip()
            listing_date = _date_value(row.get("A股上市日期"))
            if (
                code
                and listing_date is not None
                and listing_date <= range_end
                and not _is_excluded_name(name)
            ):
                listings.append(
                    SecurityListing(code, f"sz{code}", name, listing_date)
                )
        return sorted(listings, key=lambda listing: listing.code)

    def _load_symbol_facts(
        self,
        listing: SecurityListing,
        range_start: date,
        range_end: date,
        trading_dates: list[date],
        new_stock_exclusion_days: int,
    ) -> list[HistoricalSecurityFactRow]:
        frame = self._stock_history(
            listing.symbol,
            range_start - timedelta(days=14),
            range_end,
        ).copy()
        if frame.empty:
            if listing.listing_date < range_start:
                raise HistoryDataQualityError(
                    f"{listing.code} 未返回过去一年历史行情"
                )
            return []
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        frame["previous_close"] = pd.to_numeric(
            frame["close"], errors="coerce"
        ).shift(1)
        facts: list[HistoricalSecurityFactRow] = []
        for row in frame.to_dict("records"):
            trading_date = _date_value(row.get("date"))
            if trading_date is None or not range_start <= trading_date <= range_end:
                continue
            listing_trading_days = sum(
                listing.listing_date <= item <= trading_date for item in trading_dates
            )
            open_price = _number(row.get("open"))
            high = _number(row.get("high"))
            low = _number(row.get("low"))
            close = _number(row.get("close"))
            previous_close = _number(row.get("previous_close"))
            volume = _integer(row.get("volume"))
            turnover_cny = _integer(row.get("amount"))
            if (
                listing_trading_days < new_stock_exclusion_days
                or volume <= 0
                or turnover_cny <= 0
                or previous_close <= 0
                or not all(price > 0 for price in (open_price, high, low, close))
            ):
                continue
            facts.append(
                HistoricalSecurityFactRow(
                    actual_data_date=trading_date,
                    code=listing.code,
                    name=listing.name,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    previous_close=previous_close,
                    change_pct=(close / previous_close - 1) * 100,
                    volume=volume,
                    turnover_cny=turnover_cny,
                    listing_trading_days=listing_trading_days,
                )
            )
        return facts

    def _publish_complete_days(
        self,
        *,
        path: Path,
        trading_dates: list[date],
        benchmark_by_date: dict[date, dict[str, object]],
        collected_at: datetime,
    ) -> int:
        daily_batches: list[DailyMarketFacts] = []
        for trading_date in trading_dates:
            cached = historical_security_facts_for_date(
                path,
                source=HISTORY_SOURCE,
                actual_data_date=trading_date,
            )
            if not cached:
                raise HistoryDataQualityError(
                    f"{trading_date.isoformat()} 没有合格个股，拒绝发布历史批次"
                )
            benchmark = benchmark_by_date[trading_date]
            daily_batches.append(
                DailyMarketFacts(
                    source="akshare",
                    requested_date=trading_date,
                    actual_data_date=trading_date,
                    benchmark=BenchmarkDailyFact(
                        name="沪深 300",
                        date=trading_date,
                        open=_number(benchmark.get("open")),
                        high=_number(benchmark.get("high")),
                        low=_number(benchmark.get("low")),
                        close=_number(benchmark.get("close")),
                        volume=_integer(benchmark.get("volume")),
                    ),
                    securities=[
                        SecurityDailyFact(
                            code=fact.code,
                            name=fact.name,
                            date=fact.actual_data_date,
                            open=fact.open,
                            high=fact.high,
                            low=fact.low,
                            close=fact.close,
                            previous_close=fact.previous_close,
                            change_pct=fact.change_pct,
                            volume=fact.volume,
                            turnover_cny=fact.turnover_cny,
                            listing_trading_days=fact.listing_trading_days,
                        )
                        for fact in cached
                    ],
                )
            )
        for batch in daily_batches:
            save_historical_benchmark_fact(
                path,
                source=HISTORY_SOURCE,
                fact=HistoricalBenchmarkFactRow(
                    actual_data_date=batch.actual_data_date,
                    name=batch.benchmark.name,
                    open=batch.benchmark.open,
                    high=batch.benchmark.high,
                    low=batch.benchmark.low,
                    close=batch.benchmark.close,
                    volume=batch.benchmark.volume,
                ),
            )
            advancers = sum(
                security.change_pct > 0 for security in batch.securities
            )
            decliners = sum(
                security.change_pct < 0 for security in batch.securities
            )
            save_historical_market_summary(
                path,
                source=HISTORY_SOURCE,
                summary=HistoricalMarketSummaryRow(
                    actual_data_date=batch.actual_data_date,
                    benchmark_close=batch.benchmark.close,
                    turnover_cny=sum(
                        security.turnover_cny for security in batch.securities
                    ),
                    security_count=len(batch.securities),
                    advancers=advancers,
                    decliners=decliners,
                    unchanged=len(batch.securities) - advancers - decliners,
                ),
            )
            save_market_facts(
                path,
                actual_data_date=batch.actual_data_date,
                source=batch.source,
                payload_json=batch.model_dump_json(),
                collected_at=collected_at,
            )
        return len(daily_batches)


def build_akshare_one_year_history_importer(
    *,
    max_workers: int = 2,
    progress_callback: ProgressCallback | None = None,
) -> AkshareOneYearHistoryImporter:
    import akshare as ak  # type: ignore[import-untyped]

    return AkshareOneYearHistoryImporter(
        sh_listing=lambda: ak.stock_info_sh_name_code(symbol="主板A股"),
        sz_listing=lambda: ak.stock_info_sz_name_code(symbol="A股列表"),
        index_history=lambda: ak.stock_zh_index_daily(symbol="sh000300"),
        stock_history=lambda symbol, start, end: ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        ),
        max_workers=max_workers,
        progress_callback=progress_callback,
    )


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)


def _normalise_code(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text.zfill(6) if text.isdigit() else text


def _date_value(value: object) -> date | None:
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    result = parsed.date()
    return result if isinstance(result, date) else None


def _number(value: object) -> float:
    numeric = pd.to_numeric(str(value), errors="coerce")
    return 0.0 if pd.isna(numeric) else float(numeric)


def _integer(value: object) -> int:
    return max(0, round(_number(value)))


def _is_excluded_name(name: str) -> bool:
    return "ST" in name.upper() or "退" in name
