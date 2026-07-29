from __future__ import annotations

import argparse
import json
import os
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
    verified_empty_dividend_frame,
)
from fourseasquant.fundamental_capital_actions import (  # noqa: E402
    CapitalActionEvent,
    aggregate_shareholder_actions,
    calculate_average_floating_market_cap,
)
from fourseasquant.fundamental_mechanical import (  # noqa: E402
    PersonalFundamentalMonthlyInput,
    PersonalFundamentalMonthlySnapshot,
    calculate_personal_fundamental_monthly_snapshot,
)
from fourseasquant.fundamental_queries import (  # noqa: E402
    read_monthly_fundamental_series,
)
from fourseasquant.fundamental_repository import (  # noqa: E402
    read_latest_published_capital_action_snapshot,
    save_personal_fundamental_monthly_batch,
)
from fourseasquant.sqlite_connection import open_database_connection  # noqa: E402

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
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    arguments = parser.parse_args()
    _disable_application_proxy_environment()

    path = database_path()
    initialize_database(path)
    as_of_date = arguments.as_of or _latest_market_date(path)
    capital_snapshot = read_latest_published_capital_action_snapshot(
        path,
        as_of_date=as_of_date,
    )
    if (
        capital_snapshot is None
        or capital_snapshot.as_of_date != as_of_date
    ):
        print(
            json.dumps(
                {
                    "as_of_date": as_of_date.isoformat(),
                    "requested": 0,
                    "collected": 0,
                    "stored": 0,
                    "unchanged": 0,
                    "failed": 1,
                    "errors": {
                        "__global__": "缺少目标日期完全一致的已发布资本行为批次"
                    },
                    "published": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    codes = (
        sorted(set(arguments.codes))
        if arguments.codes
        else (
            capital_snapshot.expected_codes[: arguments.limit]
        )
    )
    if set(codes) != set(capital_snapshot.expected_codes):
        print(
            json.dumps(
                {
                    "as_of_date": as_of_date.isoformat(),
                    "requested": len(codes),
                    "collected": 0,
                    "stored": 0,
                    "unchanged": 0,
                    "failed": 1,
                    "errors": {
                        "__global__": (
                            "月度正式发布必须覆盖资本行为批次的全部候选股票"
                        )
                    },
                    "published": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    capital_events_by_code: dict[str, list[CapitalActionEvent]] = {}
    for event in capital_snapshot.events:
        capital_events_by_code.setdefault(event.code, []).append(event)
    capital_covered_codes = set(capital_snapshot.completed_codes)
    collected: list[tuple[str, PersonalFundamentalMonthlyInput]] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, arguments.workers)) as executor:
        futures = {
            executor.submit(
                _collect_one,
                path,
                code,
                as_of_date,
                capital_events_by_code.get(code, []),
                code in capital_covered_codes,
                capital_snapshot.insider_window_complete,
            ): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                collected.append((code, future.result()))
            except Exception as error:
                errors[code] = f"{type(error).__name__}: {error}"

    if errors:
        print(
            json.dumps(
                {
                    "as_of_date": as_of_date.isoformat(),
                    "requested": len(codes),
                    "collected": len(collected),
                    "stored": 0,
                    "unchanged": 0,
                    "failed": len(errors),
                    "errors": errors,
                    "published": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 1

    created_at = datetime.now(BEIJING)
    # 候选池不能冒充全部正常交易 A 股横截面。当前可靠来源只能覆盖
    # 最多 200 只候选，因此在全市场流通股本快照接入前保持百分位为空。
    floating_market_cap_universe: list[float] = []
    prepared: list[
        tuple[
            PersonalFundamentalMonthlySnapshot,
            list[str],
        ]
    ] = []
    for code, source in sorted(collected):
        source = source.model_copy(
            update={
                "floating_market_cap_universe": floating_market_cap_universe,
            }
        )
        snapshot = calculate_personal_fundamental_monthly_snapshot(source)
        capital_source_urls = [
            event.source_url
            for event in capital_events_by_code.get(code, [])
        ]
        prepared.append(
            (
                snapshot,
                [*SOURCE_URLS, *capital_source_urls],
            )
        )
    batch_result = save_personal_fundamental_monthly_batch(
        path,
        prepared,
        created_at=created_at,
    )
    stored = batch_result.stored_count
    unchanged = len(prepared) - stored
    for snapshot, _ in prepared:
        result = batch_result.results[snapshot.code]
        if result.stored:
            status = "stored"
        else:
            status = "unchanged"
        print(
            json.dumps(
                {
                    "code": snapshot.code,
                    "stored": result.stored,
                    "status": status,
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
                "published": True,
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
    capital_events: list[CapitalActionEvent],
    capital_coverage_complete: bool,
    insider_window_complete: bool,
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
        dividends=_fetch_dividends(code),
        business_segments=_retry(
            f"{code} 东方财富主营构成",
            lambda: cast(
                pd.DataFrame,
                ak.stock_zygc_em(symbol=f"{eastmoney_prefix}{code}"),
            ),
        ),
    )
    shareholder_actions = (
        aggregate_shareholder_actions(
            events=capital_events,
            as_of_date=as_of_date,
            insider_window_complete=insider_window_complete,
            average_floating_market_cap=(
                calculate_average_floating_market_cap(
                    closes=_close_history(path, code, as_of_date),
                    share_changes=frames.share_changes,
                    as_of_date=as_of_date,
                )
            ),
        )
        if capital_coverage_complete
        else None
    )
    historical_adjusted_pe, historical_pretax_margins = _historical_values(
        path,
        code,
        as_of_date,
    )
    source = build_monthly_input_from_akshare(
        code=code,
        as_of_date=as_of_date,
        price=_latest_close(path, code, as_of_date),
        frames=frames,
        shareholder_actions=shareholder_actions,
    )
    return source.model_copy(
        update={
            "historical_adjusted_pe": historical_adjusted_pe,
            "historical_pretax_margins": historical_pretax_margins,
        }
    )


def _fetch_dividends(code: str) -> pd.DataFrame:
    try:
        return _retry(
            f"{code} 巨潮分红",
            lambda: cast(
                pd.DataFrame,
                ak.stock_dividend_cninfo(symbol=code),
            ),
        )
    except KeyError as error:
        if error.args != ("实施方案公告日期",):
            raise
        fallback = _retry(
            f"{code} 东方财富分红核验",
            lambda: cast(
                pd.DataFrame,
                ak.stock_history_dividend_detail(
                    symbol=code,
                    indicator="分红",
                ),
            ),
        )
        return verified_empty_dividend_frame(fallback)


def _latest_market_date(path: Path) -> date:
    with open_database_connection(path) as connection:
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
    with open_database_connection(path) as connection:
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
    with open_database_connection(path) as connection:
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


def _close_history(path: Path, code: str, as_of_date: date) -> pd.DataFrame:
    start_date = _one_year_before(as_of_date)
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT actual_data_date, close
            FROM historical_security_facts
            WHERE source = ? AND code = ?
              AND actual_data_date BETWEEN ? AND ?
            ORDER BY actual_data_date
            """,
            (
                HISTORY_SOURCE,
                code,
                start_date.isoformat(),
                as_of_date.isoformat(),
            ),
        ).fetchall()
    return pd.DataFrame(rows, columns=["actual_data_date", "close"])


def _one_year_before(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _historical_values(
    path: Path,
    code: str,
    as_of_date: date,
) -> tuple[list[float], list[float]]:
    series = read_monthly_fundamental_series(
        path,
        code,
        target_date=as_of_date,
        limit=120,
    )
    adjusted_pe = [
        record.snapshot.adjusted_pe
        for record in series.records
        if record.snapshot.as_of_date < as_of_date
        and record.snapshot.adjusted_pe is not None
        and record.snapshot.adjusted_pe > 0
    ]
    pretax_margins = [
        record.snapshot.pretax_margin
        for record in series.records
        if record.snapshot.as_of_date < as_of_date
        and record.snapshot.pretax_margin is not None
    ]
    return (
        [float(value) for value in adjusted_pe],
        [float(value) for value in pretax_margins],
    )


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
