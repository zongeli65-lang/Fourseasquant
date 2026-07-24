from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar, cast
from zoneinfo import ZoneInfo

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.akshare_history import HISTORY_SOURCE  # noqa: E402
from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.fundamental_akshare import (  # noqa: E402
    AKShareFundamentalFrames,
    build_monthly_input_from_akshare,
)
from fourseasquant.fundamental_mechanical import (  # noqa: E402
    PersonalFundamentalMonthlyInput,
    calculate_personal_fundamental_monthly_snapshot,
)
from fourseasquant.fundamental_repository import (  # noqa: E402
    save_personal_fundamental_monthly_snapshot,
)

Result = TypeVar("Result")
BEIJING = ZoneInfo("Asia/Shanghai")
SOURCE_URLS = [
    "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022",
    "https://webapi.cninfo.com.cn/api/stock/p_stock2215",
    "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139",
    "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 AKShare 真实数据生成个股月度基本面快照"
    )
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    arguments = parser.parse_args()
    _disable_application_proxy_environment()

    path = database_path()
    initialize_database(path)
    as_of_date = arguments.as_of or _latest_market_date(path)
    codes = (
        sorted(set(arguments.codes))
        if arguments.codes
        else _market_codes(path, as_of_date)[: arguments.limit]
    )
    collected: list[tuple[str, PersonalFundamentalMonthlyInput]] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, arguments.workers)) as executor:
        futures = {
            executor.submit(_collect_one, path, code, as_of_date): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                collected.append((code, future.result()))
            except Exception as error:
                errors[code] = f"{type(error).__name__}: {error}"

    stored = 0
    unchanged = 0
    created_at = datetime.now(BEIJING)
    for code, source in sorted(collected):
        snapshot = calculate_personal_fundamental_monthly_snapshot(source)
        result = save_personal_fundamental_monthly_snapshot(
            path,
            snapshot,
            source_urls=SOURCE_URLS,
            created_at=created_at,
        )
        if result.stored:
            stored += 1
        else:
            unchanged += 1
        print(
            json.dumps(
                {
                    "code": code,
                    "stored": result.stored,
                    "as_of_date": snapshot.as_of_date.isoformat(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "as_of_date": as_of_date.isoformat(),
                "requested": len(codes),
                "collected": len(collected),
                "stored": stored,
                "unchanged": unchanged,
                "failed": len(errors),
                "errors": errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if collected else 1


def _collect_one(
    path: Path,
    code: str,
    as_of_date: date,
) -> PersonalFundamentalMonthlyInput:
    market_prefix = "sh" if code.startswith(("6", "68")) else "sz"
    eastmoney_prefix = market_prefix.upper()
    frames = AKShareFundamentalFrames(
        abstract=_retry(
            f"{code} 新浪关键指标",
            lambda: cast(pd.DataFrame, ak.stock_financial_abstract(symbol=code)),
        ),
        balance_sheet=_retry(
            f"{code} 新浪资产负债表",
            lambda: cast(
                pd.DataFrame,
                ak.stock_financial_report_sina(
                    stock=f"{market_prefix}{code}",
                    symbol="资产负债表",
                ),
            ),
        ),
        profit_sheet=_retry(
            f"{code} 新浪利润表",
            lambda: cast(
                pd.DataFrame,
                ak.stock_financial_report_sina(
                    stock=f"{market_prefix}{code}",
                    symbol="利润表",
                ),
            ),
        ),
        cash_flow_sheet=_retry(
            f"{code} 新浪现金流量表",
            lambda: cast(
                pd.DataFrame,
                ak.stock_financial_report_sina(
                    stock=f"{market_prefix}{code}",
                    symbol="现金流量表",
                ),
            ),
        ),
        share_changes=_retry(
            f"{code} 巨潮股本变动",
            lambda: cast(
                pd.DataFrame,
                ak.stock_share_change_cninfo(
                    symbol=code,
                    start_date="20000101",
                    end_date=as_of_date.strftime("%Y%m%d"),
                ),
            ),
        ),
        dividends=_retry(
            f"{code} 巨潮分红",
            lambda: cast(pd.DataFrame, ak.stock_dividend_cninfo(symbol=code)),
        ),
        business_segments=_retry(
            f"{code} 东方财富主营构成",
            lambda: cast(
                pd.DataFrame,
                ak.stock_zygc_em(symbol=f"{eastmoney_prefix}{code}"),
            ),
        ),
    )
    return build_monthly_input_from_akshare(
        code=code,
        as_of_date=as_of_date,
        price=_latest_close(path, code, as_of_date),
        frames=frames,
    )


def _latest_market_date(path: Path) -> date:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM historical_security_facts
            WHERE source = ?
            """,
            (HISTORY_SOURCE,),
        ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("尚无正式行情，不能生成基本面快照")
    return date.fromisoformat(cast(str, row[0]))


def _market_codes(path: Path, as_of_date: date) -> list[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT code
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            ORDER BY code
            """,
            (HISTORY_SOURCE, as_of_date.isoformat()),
        ).fetchall()
    return [cast(str, row[0]) for row in rows]


def _latest_close(path: Path, code: str, as_of_date: date) -> float:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT close
            FROM historical_security_facts
            WHERE source = ? AND code = ? AND actual_data_date <= ?
            ORDER BY actual_data_date DESC
            LIMIT 1
            """,
            (HISTORY_SOURCE, code, as_of_date.isoformat()),
        ).fetchone()
    if row is None:
        raise ValueError(f"{code} 缺少目标日期前正式收盘价")
    return float(row[0])


def _disable_application_proxy_environment() -> None:
    for key in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def _retry(
    label: str,
    operation: Callable[[], Result],
    *,
    attempts: int = 3,
) -> Result:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            delay = float(attempt)
            print(f"{label} 请求失败，{delay:.0f} 秒后重试。", flush=True)
            time.sleep(delay)
    assert last_error is not None
    raise last_error


if __name__ == "__main__":
    raise SystemExit(main())
