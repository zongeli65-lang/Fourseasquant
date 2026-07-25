from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import pandas as pd

from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    AuditStatus,
    LynchFinancialBase,
)


FINANCIAL_SOURCE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "NewFinanceAnalysis/Index"
)
DIVIDEND_SOURCE_URL = "https://webapi.cninfo.com.cn/#/company"
REQUIRED_FINANCIAL_COLUMNS = {
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "REPORT_DATE",
    "REPORT_TYPE",
    "NOTICE_DATE",
    "EPSKCJB",
}
REQUIRED_DIVIDEND_COLUMNS = {"派息日", "派息比例"}


def build_lynch_financial_base_from_akshare(
    *,
    code: str,
    as_of_date: date,
    financial: pd.DataFrame,
    dividends: pd.DataFrame,
    audit_status: AuditStatus,
    performance_forecast_blocked: bool,
    major_risk_blocked: bool,
    risk_reasons: list[str] | None = None,
) -> LynchFinancialBase:
    missing_financial = REQUIRED_FINANCIAL_COLUMNS.difference(
        financial.columns
    )
    if missing_financial:
        raise ValueError(
            f"主要财务指标缺少字段: {sorted(missing_financial)}"
        )
    missing_dividend = REQUIRED_DIVIDEND_COLUMNS.difference(dividends.columns)
    if missing_dividend:
        raise ValueError(f"分红数据缺少字段: {sorted(missing_dividend)}")

    eligible = financial.copy()
    eligible["_report_date"] = pd.to_datetime(
        eligible["REPORT_DATE"], errors="coerce"
    ).dt.date
    eligible["_notice_date"] = pd.to_datetime(
        eligible["NOTICE_DATE"], errors="coerce"
    ).dt.date
    eligible["_adjusted_eps"] = pd.to_numeric(
        eligible["EPSKCJB"], errors="coerce"
    )
    eligible = eligible[
        eligible["_report_date"].notna()
        & eligible["_notice_date"].notna()
        & (eligible["_report_date"] <= as_of_date)
        & (eligible["_notice_date"] <= as_of_date)
        & eligible["_adjusted_eps"].notna()
    ].copy()
    if eligible.empty:
        raise ValueError("目标日期前没有已公告的扣非每股收益")
    eligible.sort_values(
        ["_report_date", "_notice_date"],
        ascending=[False, False],
        inplace=True,
    )
    eligible.drop_duplicates("_report_date", keep="first", inplace=True)
    latest = eligible.iloc[0]
    latest_report_date = cast(date, latest["_report_date"])
    latest_notice_date = cast(date, latest["_notice_date"])
    eps_by_date = {
        cast(date, row["_report_date"]): float(row["_adjusted_eps"])
        for _, row in eligible.iterrows()
    }
    ttm_adjusted_eps = _ttm_eps(eps_by_date, latest_report_date)
    prior_ttm_adjusted_eps = _optional_ttm_eps(
        eps_by_date,
        latest_report_date.replace(year=latest_report_date.year - 1),
    )
    annual_rows = eligible[
        eligible["_report_date"].map(
            lambda value: value.month == 12 and value.day == 31
        )
    ].sort_values("_report_date")
    annual_rows = annual_rows.tail(3)
    annual_adjusted_eps = [
        AnnualAdjustedEps(
            year=cast(date, row["_report_date"]).year,
            value=float(row["_adjusted_eps"]),
        )
        for _, row in annual_rows.iterrows()
    ]
    name = str(latest["SECURITY_NAME_ABBR"]).strip()
    if not name:
        raise ValueError("主要财务指标缺少股票简称")
    return LynchFinancialBase(
        code=code,
        name=name,
        financial_as_of=latest_report_date,
        latest_notice_date=latest_notice_date,
        annual_adjusted_eps=annual_adjusted_eps,
        ttm_adjusted_eps=ttm_adjusted_eps,
        prior_ttm_adjusted_eps=prior_ttm_adjusted_eps,
        ttm_dividend_per_share=_ttm_dividend_per_share(
            dividends,
            as_of_date,
        ),
        audit_status=audit_status,
        performance_forecast_blocked=performance_forecast_blocked,
        major_risk_blocked=major_risk_blocked,
        risk_reasons=list(risk_reasons or []),
        source_urls=[FINANCIAL_SOURCE_URL, DIVIDEND_SOURCE_URL],
    )


def _ttm_eps(values: dict[date, float], report_date: date) -> float:
    current = values.get(report_date)
    if current is None:
        raise ValueError(f"缺少 {report_date.isoformat()} 扣非每股收益")
    if report_date.month == 12 and report_date.day == 31:
        return current
    prior_annual = date(report_date.year - 1, 12, 31)
    prior_period = report_date.replace(year=report_date.year - 1)
    annual_value = values.get(prior_annual)
    prior_period_value = values.get(prior_period)
    if annual_value is None or prior_period_value is None:
        raise ValueError("无法使用同期数据构造 TTM 扣非每股收益")
    return current + annual_value - prior_period_value


def _optional_ttm_eps(
    values: dict[date, float],
    report_date: date,
) -> float | None:
    try:
        return _ttm_eps(values, report_date)
    except ValueError:
        return None


def _ttm_dividend_per_share(
    dividends: pd.DataFrame,
    as_of_date: date,
) -> float:
    if dividends.empty:
        return 0.0
    paid_at = pd.to_datetime(dividends["派息日"], errors="coerce")
    raw_per_ten_shares = dividends["派息比例"]
    per_ten_shares = pd.to_numeric(raw_per_ten_shares, errors="coerce")
    invalid_amount = (
        paid_at.notna()
        & raw_per_ten_shares.notna()
        & per_ten_shares.isna()
    )
    if invalid_amount.any():
        raise ValueError("分红数据存在无法解析的派息比例")
    start = pd.Timestamp(as_of_date - timedelta(days=365))
    end = pd.Timestamp(as_of_date)
    eligible = per_ten_shares[
        paid_at.notna()
        & (paid_at > start)
        & (paid_at <= end)
        & (per_ten_shares > 0)
    ]
    return float(eligible.sum()) / 10
