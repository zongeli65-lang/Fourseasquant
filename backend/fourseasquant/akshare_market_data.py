from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from fourseasquant.database import save_market_facts


FrameFactory = Callable[[], pd.DataFrame]


class BenchmarkDailyFact(BaseModel):
    name: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)


class SecurityDailyFact(BaseModel):
    code: str
    name: str
    date: date
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    change_pct: float
    volume: int = Field(ge=0)
    turnover_cny: int = Field(ge=0)
    listing_trading_days: int = Field(ge=0)


class DailyMarketFacts(BaseModel):
    source: str
    requested_date: date
    actual_data_date: date
    benchmark: BenchmarkDailyFact
    securities: list[SecurityDailyFact]


class MarketDataQualityError(RuntimeError):
    """上游请求成功但数据不满足完整发布条件。"""


class MarketDataSourceError(RuntimeError):
    """AKShare 或其上游数据网站不可用。"""


class DailyFactsLoader(Protocol):
    def load_daily_facts(
        self,
        requested_date: date,
        *,
        new_stock_exclusion_days: int,
    ) -> DailyMarketFacts: ...


class AkshareMarketDataAdapter:
    """将多个 AKShare 上游隐藏在一个个股日频事实接口之后。"""

    def __init__(
        self,
        *,
        stock_snapshot: FrameFactory,
        sh_listing: FrameFactory,
        sz_listing: FrameFactory,
        index_history: FrameFactory,
    ) -> None:
        self._stock_snapshot = stock_snapshot
        self._sh_listing = sh_listing
        self._sz_listing = sz_listing
        self._index_history = index_history

    def load_daily_facts(
        self,
        requested_date: date,
        *,
        new_stock_exclusion_days: int,
    ) -> DailyMarketFacts:
        try:
            return self._load_daily_facts(
                requested_date,
                new_stock_exclusion_days=new_stock_exclusion_days,
            )
        except MarketDataQualityError:
            raise
        except Exception as error:
            raise MarketDataSourceError("AKShare 真实数据读取失败") from error

    def _load_daily_facts(
        self,
        requested_date: date,
        *,
        new_stock_exclusion_days: int,
    ) -> DailyMarketFacts:
        index_history = self._index_history().copy()
        index_history["date"] = pd.to_datetime(
            index_history["date"], errors="coerce"
        ).dt.date
        available_index = index_history[index_history["date"] <= requested_date]
        if available_index.empty:
            raise ValueError("沪深 300 在目标日期前没有可用数据")

        benchmark_row = available_index.sort_values("date").iloc[-1]
        actual_data_date = benchmark_row["date"]
        if not isinstance(actual_data_date, date):
            raise ValueError("沪深 300 数据日期无效")
        latest_available_date = index_history["date"].dropna().max()
        if (
            isinstance(latest_available_date, date)
            and actual_data_date < latest_available_date
        ):
            raise MarketDataQualityError("AKShare 最新快照不能用于历史日期")

        trading_dates = sorted(
            item
            for item in available_index["date"].dropna().tolist()
            if isinstance(item, date)
        )
        listing_dates = self._main_board_listing_dates()
        snapshot = self._stock_snapshot().copy()
        securities: list[SecurityDailyFact] = []
        for row in snapshot.to_dict("records"):
            code = _normalise_code(row.get("代码"))
            listing_date = listing_dates.get(code)
            if listing_date is None:
                continue
            name = str(row.get("名称", "")).strip()
            if _is_excluded_name(name):
                continue

            volume = _integer(row.get("成交量"))
            turnover_cny = _integer(row.get("成交额"))
            open_price = _number(row.get("今开"))
            high = _number(row.get("最高"))
            low = _number(row.get("最低"))
            if volume <= 0 or turnover_cny <= 0 or not all(
                price > 0 for price in (open_price, high, low)
            ):
                continue

            listing_trading_days = sum(
                listing_date <= trading_date <= actual_data_date
                for trading_date in trading_dates
            )
            if listing_trading_days < new_stock_exclusion_days:
                continue

            securities.append(
                SecurityDailyFact(
                    code=code,
                    name=name,
                    date=actual_data_date,
                    open=open_price,
                    high=high,
                    low=low,
                    close=_number(row.get("最新价")),
                    previous_close=_number(row.get("昨收")),
                    change_pct=_number(row.get("涨跌幅")),
                    volume=volume,
                    turnover_cny=turnover_cny,
                    listing_trading_days=listing_trading_days,
                )
            )

        securities.sort(key=lambda security: security.code)
        if not securities:
            raise MarketDataQualityError("没有合格的沪深主板个股")
        return DailyMarketFacts(
            source="akshare",
            requested_date=requested_date,
            actual_data_date=actual_data_date,
            benchmark=BenchmarkDailyFact(
                name="沪深 300",
                date=actual_data_date,
                open=_number(benchmark_row["open"]),
                high=_number(benchmark_row["high"]),
                low=_number(benchmark_row["low"]),
                close=_number(benchmark_row["close"]),
                volume=_integer(benchmark_row["volume"]),
            ),
            securities=securities,
        )

    def _main_board_listing_dates(self) -> dict[str, date]:
        listing_dates: dict[str, date] = {}
        for row in self._sh_listing().to_dict("records"):
            code = _normalise_code(row.get("证券代码"))
            listing_date = _date_value(row.get("上市日期"))
            if code and listing_date:
                listing_dates[code] = listing_date

        sz_listing = self._sz_listing()
        if "板块" in sz_listing.columns:
            sz_listing = sz_listing[sz_listing["板块"] == "主板"]
        for row in sz_listing.to_dict("records"):
            code = _normalise_code(row.get("A股代码"))
            listing_date = _date_value(row.get("A股上市日期"))
            if code and listing_date:
                listing_dates[code] = listing_date
        return listing_dates


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
    upper_name = name.upper()
    return "ST" in upper_name or "退" in name


def build_akshare_market_data_adapter() -> AkshareMarketDataAdapter:
    import akshare as ak  # type: ignore[import-untyped]

    return AkshareMarketDataAdapter(
        stock_snapshot=ak.stock_zh_a_spot,
        sh_listing=lambda: ak.stock_info_sh_name_code(symbol="主板A股"),
        sz_listing=lambda: ak.stock_info_sz_name_code(symbol="A股列表"),
        index_history=lambda: ak.stock_zh_index_daily(symbol="sh000300"),
    )


def collect_and_store_daily_facts(
    requested_date: date,
    *,
    path: Path,
    new_stock_exclusion_days: int,
    adapter: DailyFactsLoader | None = None,
    collected_at: datetime | None = None,
) -> DailyMarketFacts:
    selected_adapter = adapter or build_akshare_market_data_adapter()
    facts = selected_adapter.load_daily_facts(
        requested_date,
        new_stock_exclusion_days=new_stock_exclusion_days,
    )
    save_market_facts(
        path,
        actual_data_date=facts.actual_data_date,
        source=facts.source,
        payload_json=facts.model_dump_json(),
        collected_at=collected_at or datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    return facts
