from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd
import requests

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.fundamental_lynch import LynchFinancialBase  # noqa: E402
from fourseasquant.fundamental_lynch_akshare import (  # noqa: E402
    REQUIRED_DIVIDEND_COLUMNS,
    build_lynch_financial_base_from_akshare,
    latest_eligible_financial_report_date,
)
from fourseasquant.fundamental_lynch_repository import (  # noqa: E402
    read_lynch_financial_collection_cache,
    save_lynch_financial_collection_item,
    save_lynch_financial_base_batch,
)
from fourseasquant.fundamental_lynch_service import (  # noqa: E402
    LynchMarketSecurity,
    read_lynch_market_universe,
    unavailable_financial_base,
)


BEIJING = ZoneInfo("Asia/Shanghai")
LEGACY_LYNCH_RULES_VERSION = "lynch-market-v1"
BALANCE_SHEET_ENDPOINT = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "NewFinanceAnalysis/zcfzbAjaxNew"
)
COMPANY_TYPE_BY_ORG_TYPE = {
    "证券": "1",
    "保险": "2",
    "银行": "3",
    "通用": "4",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用 AKShare 初始化全市场林奇财务基座"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--refresh", action="store_true")
    arguments = parser.parse_args()
    _install_request_timeout()
    _disable_application_proxy_environment()
    path = database_path()
    initialize_database(path)
    requested_date = arguments.as_of or date.today()
    universe = read_lynch_market_universe(path, requested_date)
    selected = [
        item
        for item in universe.securities
        if not arguments.codes or item.code in set(arguments.codes)
    ]
    if arguments.codes and len(selected) != len(set(arguments.codes)):
        print("指定代码不全在目标日期的合规股票范围内", file=sys.stderr)
        return 2
    expected_codes = [item.code for item in selected]
    cached = (
        {}
        if arguments.refresh
        else read_lynch_financial_collection_cache(
            path, target_date=universe.actual_data_date
        )
    )
    completed = {
        code: base for code, base in cached.items() if code in expected_codes
    }
    pending = [item for item in selected if item.code not in completed]
    print(
        json.dumps(
            {
                "stage": "start",
                "target_date": universe.actual_data_date.isoformat(),
                "expected": len(expected_codes),
                "resumed": len(completed),
                "pending": len(pending),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        dividends: dict[str, pd.DataFrame] | None = (
            _collect_bulk_dividends(universe.actual_data_date)
        )
    except requests.RequestException as error:
        print(
            json.dumps(
                {
                    "stage": "dividend_fallback",
                    "reason": f"{type(error).__name__}: {error}",
                    "pending": len(pending),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        dividends = _legacy_dividends(
            path,
            universe.actual_data_date,
            pending,
        )
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, arguments.workers)) as executor:
        futures: dict[Future[LynchFinancialBase], LynchMarketSecurity] = {
            executor.submit(
                _collect_one_with_retry,
                item,
                universe.actual_data_date,
                (
                    dividends.get(item.code, _empty_dividends())
                    if dividends is not None
                    else None
                ),
            ): item
            for item in pending
        }
        for index, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            try:
                base = future.result()
                completed[item.code] = base
                save_lynch_financial_collection_item(
                    path,
                    target_date=universe.actual_data_date,
                    base=base,
                    collected_at=datetime.now(BEIJING),
                )
            except Exception as error:
                errors[item.code] = f"{type(error).__name__}: {error}"
            if index % 100 == 0 or index == len(pending):
                print(
                    json.dumps(
                        {
                            "stage": "collect",
                            "processed": index,
                            "pending_total": len(pending),
                            "completed_total": len(completed),
                            "failed": len(errors),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    ordered_bases = [
        completed[code] for code in expected_codes if code in completed
    ]
    saved = save_lynch_financial_base_batch(
        path,
        target_date=universe.actual_data_date,
        expected_codes=expected_codes,
        bases=ordered_bases,
        errors=errors,
        collected_at=datetime.now(BEIJING),
    )
    unavailable = sum(
        item.financial_unavailable_reason is not None for item in ordered_bases
    )
    print(
        json.dumps(
            {
                "stage": "complete",
                "target_date": universe.actual_data_date.isoformat(),
                "batch_id": saved.batch_id,
                "expected": saved.expected_count,
                "completed": saved.completed_count,
                "unavailable": unavailable,
                "failed": len(errors),
                "published": saved.published,
                "errors": errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if saved.published else 1


def _collect_one_with_retry(
    security: LynchMarketSecurity,
    as_of_date: date,
    dividends: pd.DataFrame | None,
) -> LynchFinancialBase:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            symbol = (
                f"{security.code}.SH"
                if security.code.startswith(("6", "9"))
                else f"{security.code}.SZ"
            )
            financial = ak.stock_financial_analysis_indicator_em(
                symbol=symbol,
                indicator="按报告期",
            )
            if financial.empty:
                return unavailable_financial_base(
                    code=security.code,
                    name=security.name,
                    reason="AKShare 返回有效空财务历史",
                )
            selected_dividends = (
                dividends
                if dividends is not None
                else cast(
                    pd.DataFrame,
                    ak.stock_dividend_cninfo(symbol=security.code),
                )
            )
            try:
                balance_sheet = _collect_balance_sheet(
                    security,
                    financial,
                    as_of_date,
                )
                return build_lynch_financial_base_from_akshare(
                    code=security.code,
                    as_of_date=as_of_date,
                    financial=financial,
                    balance_sheet=balance_sheet,
                    dividends=selected_dividends,
                    audit_status="unknown",
                    performance_forecast_blocked=False,
                    major_risk_blocked=False,
                    risk_reasons=[
                        "审计意见与重大风险公告证据尚未结构化确认"
                    ],
                )
            except ValueError as error:
                if _is_valid_financial_unavailability(str(error)):
                    return unavailable_financial_base(
                        code=security.code,
                        name=security.name,
                        reason=str(error),
                    )
                raise
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def _collect_balance_sheet(
    security: LynchMarketSecurity,
    financial: pd.DataFrame,
    as_of_date: date,
) -> pd.DataFrame:
    report_date = latest_eligible_financial_report_date(
        financial,
        as_of_date,
    )
    report_dates = pd.to_datetime(
        financial["REPORT_DATE"], errors="coerce"
    ).dt.date
    rows = financial[report_dates == report_date]
    if rows.empty or "ORG_TYPE" not in rows.columns:
        raise ValueError("主要财务指标缺少公司类型")
    org_type = str(rows.iloc[0]["ORG_TYPE"]).strip()
    company_type = COMPANY_TYPE_BY_ORG_TYPE.get(org_type)
    if company_type is None:
        raise ValueError(f"不支持的公司类型: {org_type}")
    market_code = (
        f"SH{security.code}"
        if security.code.startswith(("6", "9"))
        else f"SZ{security.code}"
    )
    response = requests.get(
        BALANCE_SHEET_ENDPOINT,
        params={
            "companyType": company_type,
            "reportDateType": "0",
            "reportType": "1",
            "dates": report_date.isoformat(),
            "code": market_code,
        },
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError(
            f"资产负债表缺少 {report_date.isoformat()} 报告期"
        )
    return pd.DataFrame(data)


def _collect_bulk_dividends(as_of_date: date) -> dict[str, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for year in range(as_of_date.year - 1, as_of_date.year + 1):
        for month_day in ("0331", "0630", "0930", "1231"):
            if year == as_of_date.year - 1 and month_day == "0331":
                continue
            report_date = f"{year}{month_day}"
            if date.fromisoformat(
                f"{year}-{month_day[:2]}-{month_day[2:]}"
            ) > as_of_date:
                continue
            frame = cast(pd.DataFrame, ak.stock_fhps_em(date=report_date))
            if frame.empty:
                continue
            required = {"代码", "现金分红-现金分红比例", "除权除息日"}
            missing = required.difference(frame.columns)
            if missing:
                raise ValueError(f"全市场分红数据缺少字段: {sorted(missing)}")
            frames.append(
                pd.DataFrame(
                    {
                        "代码": frame["代码"].astype(str).str.zfill(6),
                        "派息日": frame["除权除息日"],
                        "派息比例": frame["现金分红-现金分红比例"],
                    }
                )
            )
    if not frames:
        raise RuntimeError("AKShare 全市场分红接口没有返回任何报告期")
    combined = pd.concat(frames, ignore_index=True)
    return {
        str(code): group[list(REQUIRED_DIVIDEND_COLUMNS)].copy()
        for code, group in combined.groupby("代码")
    }


def _collect_individual_dividends(
    securities: list[LynchMarketSecurity],
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for index, security in enumerate(securities, start=1):
        try:
            source = cast(
                pd.DataFrame,
                ak.stock_history_dividend_detail(
                    symbol=security.code,
                    indicator="分红",
                ),
            )
            if source.empty:
                result[security.code] = _empty_dividends()
            else:
                required = {"除权除息日", "派息"}
                missing = required.difference(source.columns)
                if missing:
                    raise ValueError(
                        f"新浪分红数据缺少字段: {sorted(missing)}"
                    )
                result[security.code] = pd.DataFrame(
                    {
                        "派息日": source["除权除息日"],
                        "派息比例": source["派息"],
                    }
                )
        except (IndexError, KeyError, ValueError):
            result[security.code] = _empty_dividends()
        if index % 25 == 0 or index == len(securities):
            print(
                json.dumps(
                    {
                        "stage": "dividend_fallback_collect",
                        "processed": index,
                        "total": len(securities),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return result


def _legacy_dividends(
    path: Path,
    target_date: date,
    securities: list[LynchMarketSecurity],
) -> dict[str, pd.DataFrame]:
    legacy = read_lynch_financial_collection_cache(
        path,
        target_date=target_date,
        rules_version=LEGACY_LYNCH_RULES_VERSION,
    )
    missing = [item.code for item in securities if item.code not in legacy]
    if missing:
        raise RuntimeError(
            "旧正式林奇财务基座缺少股息数据: "
            + ",".join(missing[:10])
        )
    return {
        item.code: pd.DataFrame(
            [
                {
                    "派息日": target_date.isoformat(),
                    "派息比例": (
                        legacy[item.code].ttm_dividend_per_share * 10
                    ),
                }
            ]
        )
        for item in securities
    }


def _empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(columns=sorted(REQUIRED_DIVIDEND_COLUMNS))


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


def _install_request_timeout() -> None:
    original = requests.sessions.Session.request

    def request_with_timeout(
        session: requests.Session,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        kwargs.setdefault("timeout", (5, 20))
        return original(session, method, url, **kwargs)

    requests.sessions.Session.request = request_with_timeout  # type: ignore[assignment]


def _is_valid_financial_unavailability(message: str) -> bool:
    return (
        message == "目标日期前没有已公告的扣非每股收益"
        or message.startswith("缺少 ")
        or message == "无法使用同期数据构造 TTM 扣非每股收益"
    )


if __name__ == "__main__":
    raise SystemExit(main())
