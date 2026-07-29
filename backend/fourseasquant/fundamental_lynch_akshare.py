from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import pandas as pd

from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    AuditStatus,
    FinancialSafetyStatus,
    LynchFinancialBase,
)


FINANCIAL_SOURCE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "NewFinanceAnalysis/Index"
)
DIVIDEND_SOURCE_URL = "https://webapi.cninfo.com.cn/#/company"
BALANCE_SHEET_SOURCE_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "NewFinanceAnalysis/Index"
)
REQUIRED_FINANCIAL_COLUMNS = {
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "REPORT_DATE",
    "REPORT_TYPE",
    "NOTICE_DATE",
    "EPSKCJB",
}
REQUIRED_DIVIDEND_COLUMNS = {"派息日", "派息比例"}
REQUIRED_BALANCE_COLUMNS = {"REPORT_DATE", "TOTAL_PARENT_EQUITY"}
_CASH_COLUMNS = ("MONETARYFUNDS", "CASH_DEPOSIT_PBC")
_INTEREST_BEARING_DEBT_COLUMNS = (
    "SHORT_LOAN",
    "LONG_LOAN",
    "BOND_PAYABLE",
    "SHORT_BOND_PAYABLE",
    "LEASE_LIAB",
    "SHORT_FIN_PAYABLE",
    "BORROW_FUND",
    "DEPOSIT_INTERBANK",
    "ACCEPT_DEPOSIT",
)


def build_lynch_financial_base_from_akshare(
    *,
    code: str,
    as_of_date: date,
    financial: pd.DataFrame,
    balance_sheet: pd.DataFrame,
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
    missing_balance = REQUIRED_BALANCE_COLUMNS.difference(
        balance_sheet.columns
    )
    if missing_balance:
        raise ValueError(
            f"资产负债表缺少字段: {sorted(missing_balance)}"
        )

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
    net_debt_to_equity, safety_status = _net_debt_to_equity(
        balance_sheet,
        latest_report_date,
    )
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
        net_debt_to_equity=net_debt_to_equity,
        financial_safety_status=safety_status,
        audit_status=audit_status,
        performance_forecast_blocked=performance_forecast_blocked,
        major_risk_blocked=major_risk_blocked,
        risk_reasons=list(risk_reasons or []),
        source_urls=[
            FINANCIAL_SOURCE_URL,
            BALANCE_SHEET_SOURCE_URL,
            DIVIDEND_SOURCE_URL,
        ],
    )


def latest_eligible_financial_report_date(
    financial: pd.DataFrame,
    as_of_date: date,
) -> date:
    required = {"REPORT_DATE", "NOTICE_DATE", "EPSKCJB"}
    missing = required.difference(financial.columns)
    if missing:
        raise ValueError(f"主要财务指标缺少字段: {sorted(missing)}")
    report_dates = pd.to_datetime(
        financial["REPORT_DATE"], errors="coerce"
    ).dt.date
    notice_dates = pd.to_datetime(
        financial["NOTICE_DATE"], errors="coerce"
    ).dt.date
    adjusted_eps = pd.to_numeric(
        financial["EPSKCJB"], errors="coerce"
    )
    eligible = report_dates[
        report_dates.notna()
        & notice_dates.notna()
        & adjusted_eps.notna()
        & (report_dates <= as_of_date)
        & (notice_dates <= as_of_date)
    ]
    if eligible.empty:
        raise ValueError("目标日期前没有已公告的扣非每股收益")
    return eligible.max()


def _net_debt_to_equity(
    balance_sheet: pd.DataFrame,
    report_date: date,
) -> tuple[float | None, FinancialSafetyStatus]:
    dates = pd.to_datetime(
        balance_sheet["REPORT_DATE"], errors="coerce"
    ).dt.date
    rows = balance_sheet[dates == report_date]
    if rows.empty:
        raise ValueError(
            f"资产负债表缺少 {report_date.isoformat()} 报告期"
        )
    row = rows.iloc[0]
    cash = _first_reported_number(row, _CASH_COLUMNS)
    equity = _first_reported_number(row, ("TOTAL_PARENT_EQUITY",))
    if cash is None or equity is None:
        raise ValueError("资产负债表缺少现金或归母净资产")
    if equity <= 0:
        return None, "invalid_equity"
    debt_columns = [
        column
        for column in _INTEREST_BEARING_DEBT_COLUMNS
        if column in balance_sheet.columns
    ]
    if not debt_columns:
        raise ValueError("资产负债表缺少有息负债字段")
    interest_bearing_debt = sum(
        value
        for column in debt_columns
        if (value := _reported_number(row, column)) is not None
    )
    return (interest_bearing_debt - cash) / equity, "available"


def _first_reported_number(
    row: pd.Series,
    columns: tuple[str, ...],
) -> float | None:
    for column in columns:
        value = _reported_number(row, column)
        if value is not None:
            return value
    return None


def _reported_number(row: pd.Series, column: str) -> float | None:
    if column not in row.index:
        return None
    value = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


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
