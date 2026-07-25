from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from fourseasquant.akshare_history import HISTORY_SOURCE
from fourseasquant.fundamental_lynch import (
    LynchFinancialBase,
    calculate_lynch_daily_result,
    rank_lynch_results,
)
from fourseasquant.fundamental_lynch_repository import (
    LynchBatchSaveResult,
    read_latest_published_lynch_financial_batch,
    save_lynch_daily_batch,
)


BEIJING = ZoneInfo("Asia/Shanghai")
ELIGIBLE_CODE = re.compile(
    r"^(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}$"
)
MINIMUM_LISTING_TRADING_DAYS = 60
MAXIMUM_FINANCIAL_BASE_AGE_DAYS = 45


class FinancialBaseMissing(RuntimeError):
    pass


class FinancialBaseExpired(RuntimeError):
    pass


class LynchMarketSecurity(BaseModel):
    code: str
    name: str
    close: float
    listing_trading_days: int


class LynchMarketUniverse(BaseModel):
    requested_date: date
    actual_data_date: date
    securities: list[LynchMarketSecurity]


def read_lynch_market_universe(
    path: Path,
    requested_date: date,
) -> LynchMarketUniverse:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date <= ?
            """,
            (HISTORY_SOURCE, requested_date.isoformat()),
        ).fetchone()
        if row is None or row[0] is None:
            raise LookupError("目标日期前没有 AKShare 日频行情")
        actual_date = date.fromisoformat(cast(str, row[0]))
        rows = connection.execute(
            """
            SELECT code, name, close, listing_trading_days
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
              AND close > 0
              AND listing_trading_days >= ?
            ORDER BY code
            """,
            (
                HISTORY_SOURCE,
                actual_date.isoformat(),
                MINIMUM_LISTING_TRADING_DAYS,
            ),
        ).fetchall()
    securities = [
        LynchMarketSecurity(
            code=cast(str, item[0]),
            name=cast(str, item[1]),
            close=cast(float, item[2]),
            listing_trading_days=cast(int, item[3]),
        )
        for item in rows
        if _eligible_security(cast(str, item[0]), cast(str, item[1]))
    ]
    return LynchMarketUniverse(
        requested_date=requested_date,
        actual_data_date=actual_date,
        securities=securities,
    )


def unavailable_financial_base(
    *,
    code: str,
    name: str,
    reason: str = "目标财务基座没有该股票的有效记录",
) -> LynchFinancialBase:
    return LynchFinancialBase(
        code=code,
        name=name,
        financial_as_of=None,
        latest_notice_date=None,
        annual_adjusted_eps=[],
        ttm_adjusted_eps=None,
        prior_ttm_adjusted_eps=None,
        ttm_dividend_per_share=0,
        audit_status="unknown",
        financial_unavailable_reason=reason,
    )


def run_lynch_daily_calculation(
    path: Path,
    target_date: date,
    *,
    calculated_at: datetime | None = None,
) -> LynchBatchSaveResult:
    universe = read_lynch_market_universe(path, target_date)
    publication = read_latest_published_lynch_financial_batch(
        path, universe.actual_data_date
    )
    if publication is None:
        raise FinancialBaseMissing("尚无已完整发布的全市场财务基座")
    age = (universe.actual_data_date - publication.target_date).days
    if age > MAXIMUM_FINANCIAL_BASE_AGE_DAYS:
        raise FinancialBaseExpired(
            f"财务基座已过期：{age} 天，最大允许 45 天"
        )
    results = []
    for security in universe.securities:
        financial = publication.bases.get(security.code)
        if financial is None:
            financial = unavailable_financial_base(
                code=security.code,
                name=security.name,
            )
        results.append(
            calculate_lynch_daily_result(
                financial,
                target_date=universe.actual_data_date,
                close=security.close,
            )
        )
    ranked = rank_lynch_results(results)
    return save_lynch_daily_batch(
        path,
        target_date=universe.actual_data_date,
        financial_base_date=publication.target_date,
        expected_codes=[item.code for item in universe.securities],
        results=ranked,
        errors={},
        calculated_at=calculated_at or datetime.now(BEIJING),
    )


def _eligible_security(code: str, name: str) -> bool:
    normalized = name.upper().replace(" ", "")
    return (
        ELIGIBLE_CODE.fullmatch(code) is not None
        and "ST" not in normalized
        and "退" not in normalized
    )
