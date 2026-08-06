from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from fourseasquant.akshare_history import SecurityListing
from fourseasquant.database import HistoricalSecurityFactRow


@dataclass(frozen=True)
class QfqFactor:
    effective_date: date
    value: float


@dataclass(frozen=True)
class IncrementalMarketBatch:
    raw_facts: list[HistoricalSecurityFactRow]
    qfq_facts: list[HistoricalSecurityFactRow]
    missing_factor_codes: set[str]


def build_incremental_market_batch(
    snapshot: pd.DataFrame,
    *,
    target_date: date,
    listings: list[SecurityListing],
    trading_dates: list[date],
    qfq_factors: dict[str, QfqFactor],
    new_stock_exclusion_days: int,
) -> IncrementalMarketBatch:
    listing_by_code = {listing.code: listing for listing in listings}
    raw_facts: list[HistoricalSecurityFactRow] = []
    qfq_facts: list[HistoricalSecurityFactRow] = []
    missing_factor_codes: set[str] = set()
    if snapshot.empty:
        return IncrementalMarketBatch([], [], set())

    for row in snapshot.to_dict("records"):
        code = _normalise_code(row.get("代码"))
        listing = listing_by_code.get(code)
        if listing is None:
            continue
        name = str(row.get("名称", listing.name)).strip()
        if _is_excluded_name(name):
            continue
        volume = _integer(row.get("成交量"))
        turnover_cny = _integer(row.get("成交额"))
        open_price = _number(row.get("今开"))
        high = _number(row.get("最高"))
        low = _number(row.get("最低"))
        close = _number(row.get("最新价"))
        previous_close = _number(row.get("昨收"))
        if (
            volume <= 0
            or turnover_cny <= 0
            or previous_close <= 0
            or not all(
                price > 0
                for price in (open_price, high, low, close)
            )
        ):
            continue
        listing_trading_days = sum(
            listing.listing_date <= trading_date <= target_date
            for trading_date in trading_dates
        )
        if listing_trading_days < new_stock_exclusion_days:
            continue
        factor = qfq_factors.get(code)
        if factor is None or factor.value <= 0:
            missing_factor_codes.add(code)
            continue

        raw_facts.append(
            HistoricalSecurityFactRow(
                actual_data_date=target_date,
                code=code,
                name=name,
                open=open_price,
                high=high,
                low=low,
                close=close,
                previous_close=previous_close,
                change_pct=_number(row.get("涨跌幅")),
                volume=volume,
                turnover_cny=turnover_cny,
                listing_trading_days=listing_trading_days,
            )
        )
        qfq_open = round(open_price / factor.value, 2)
        qfq_high = round(high / factor.value, 2)
        qfq_low = round(low / factor.value, 2)
        qfq_close = round(close / factor.value, 2)
        qfq_previous_close = round(previous_close / factor.value, 2)
        qfq_facts.append(
            HistoricalSecurityFactRow(
                actual_data_date=target_date,
                code=code,
                name=name,
                open=qfq_open,
                high=qfq_high,
                low=qfq_low,
                close=qfq_close,
                previous_close=qfq_previous_close,
                change_pct=(
                    (qfq_close / qfq_previous_close - 1) * 100
                    if qfq_previous_close > 0
                    else 0.0
                ),
                volume=volume,
                turnover_cny=turnover_cny,
                listing_trading_days=listing_trading_days,
            )
        )

    raw_facts.sort(key=lambda fact: fact.code)
    qfq_facts.sort(key=lambda fact: fact.code)
    return IncrementalMarketBatch(
        raw_facts=raw_facts,
        qfq_facts=qfq_facts,
        missing_factor_codes=missing_factor_codes,
    )


def _normalise_code(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    return text.zfill(6) if text.isdigit() else text


def _number(value: object) -> float:
    numeric = pd.to_numeric(str(value), errors="coerce")
    return 0.0 if pd.isna(numeric) else float(numeric)


def _integer(value: object) -> int:
    return max(0, round(_number(value)))


def _is_excluded_name(name: str) -> bool:
    return "ST" in name.upper() or "退" in name
