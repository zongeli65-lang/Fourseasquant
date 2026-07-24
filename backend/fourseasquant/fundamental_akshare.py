from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast

import pandas as pd

from fourseasquant.fundamental_mechanical import (
    AnnualEarnings,
    BusinessSegment,
    PersonalFundamentalMonthlyInput,
)


@dataclass(frozen=True)
class AKShareFundamentalFrames:
    abstract: pd.DataFrame
    balance_sheet: pd.DataFrame
    profit_sheet: pd.DataFrame
    cash_flow_sheet: pd.DataFrame
    share_changes: pd.DataFrame
    dividends: pd.DataFrame
    business_segments: pd.DataFrame


def build_monthly_input_from_akshare(
    *,
    code: str,
    as_of_date: date,
    price: float,
    frames: AKShareFundamentalFrames,
) -> PersonalFundamentalMonthlyInput:
    balance = _statement_before(frames.balance_sheet, as_of_date)
    profit = _statement_before(frames.profit_sheet, as_of_date)
    cash_flow = _statement_before(frames.cash_flow_sheet, as_of_date)
    report_date = _latest_common_report_date(balance, profit, cash_flow)
    balance_row = _row_for_date(balance, report_date)
    prior_report_date = report_date.replace(year=report_date.year - 1)
    prior_balance_row = _optional_row_for_date(balance, prior_report_date)

    share_row = _latest_share_row(frames.share_changes, as_of_date)
    total_shares = _required_number(share_row, "总股本") * 10_000
    floating_shares = _required_first_number(
        share_row,
        ("人民币普通股", "已流通股份"),
    ) * 10_000

    ttm_parent_profit = _ttm_value(
        profit,
        report_date,
        (
            "归属于母公司所有者的净利润",
            "归属于母公司股东的净利润",
            "归属于母公司的净利润",
        ),
    )
    ttm_adjusted_profit = _ttm_abstract_value(
        frames.abstract,
        report_date,
        "扣非净利润",
    )
    ttm_revenue = _ttm_value(
        profit,
        report_date,
        ("营业收入", "营业总收入"),
    )
    ttm_pretax_profit = _ttm_value(profit, report_date, ("利润总额",))
    ttm_operating_cash_flow = _ttm_value(
        cash_flow,
        report_date,
        ("经营活动产生的现金流量净额", "经营活动产生的现金流量"),
    )
    ttm_capital_expenditure = _optional_nonnegative_ttm_value(
        cash_flow,
        report_date,
        (
            "购建固定资产、无形资产和其他长期资产所支付的现金",
            "购建固定资产、无形资产和其他长期资产支付的现金",
        ),
    )

    current_inventory = _optional_number(balance_row, "存货")
    prior_inventory = (
        _optional_number(prior_balance_row, "存货")
        if prior_balance_row is not None
        else None
    )
    current_revenue = _value_for_date(
        profit,
        report_date,
        ("营业收入", "营业总收入"),
    )
    prior_revenue = _optional_value_for_date(
        profit,
        prior_report_date,
        ("营业收入", "营业总收入"),
    )
    inventory_applicable = (
        current_inventory is not None or prior_inventory is not None
    )

    annual_earnings = _annual_adjusted_earnings(
        frames.abstract,
        balance,
        report_date,
    )
    annual_cash_dividends, ttm_cash_dividend = _dividend_history(
        frames.dividends,
        as_of_date,
        total_shares,
    )

    return PersonalFundamentalMonthlyInput(
        code=code,
        as_of_date=as_of_date,
        price=price,
        total_shares=total_shares,
        floating_shares=floating_shares,
        ttm_parent_net_profit=ttm_parent_profit,
        ttm_adjusted_parent_net_profit=ttm_adjusted_profit,
        cash_and_equivalents=_required_first_number(
            balance_row,
            ("货币资金", "现金及存放中央银行款项"),
        ),
        short_term_interest_bearing_debt=_sum_numbers(
            balance_row,
            ("短期借款", "一年内到期的非流动负债", "应付短期债券"),
        ),
        long_term_interest_bearing_debt=_sum_numbers(
            balance_row,
            ("长期借款", "应付债券", "租赁负债", "长期应付款"),
        ),
        parent_equity=_required_first_number(
            balance_row,
            (
                "归属于母公司股东权益合计",
                "归属于母公司所有者权益合计",
                "归属于母公司股东的权益",
                "归属于母公司的股东权益合计",
            ),
        ),
        ttm_operating_cash_flow=ttm_operating_cash_flow,
        ttm_capital_expenditure=ttm_capital_expenditure,
        ttm_pretax_profit=ttm_pretax_profit,
        ttm_revenue=ttm_revenue,
        ttm_cash_dividend=ttm_cash_dividend,
        current_inventory=current_inventory,
        prior_inventory=prior_inventory,
        inventory_applicable=inventory_applicable,
        current_revenue=current_revenue,
        prior_revenue=prior_revenue,
        annual_earnings=annual_earnings,
        annual_cash_dividends=annual_cash_dividends,
        historical_adjusted_pe=[],
        peer_adjusted_pe=[],
        historical_pretax_margins=[],
        peer_pretax_margins=[],
        business_segments=_business_segments(
            frames.business_segments,
            report_date,
        ),
        institution_holding_ratio=None,
        prior_institution_holding_ratio=None,
        shareholder_actions=None,
        floating_market_cap_universe=[],
    )


def _statement_before(frame: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    result = frame.copy()
    if "公告日期" not in result.columns:
        raise ValueError("财务报表缺少公告日期")
    result["_报告日期"] = pd.to_datetime(
        result["报告日"], format="%Y%m%d", errors="coerce"
    ).dt.date
    announcement = pd.to_datetime(
        result["公告日期"], format="%Y%m%d", errors="coerce"
    ).dt.date
    result = result[
        (result["_报告日期"] <= as_of_date)
        & (announcement.isna() | (announcement <= as_of_date))
    ]
    if result.empty:
        raise ValueError("目标日期前没有已公告财报")
    return result


def _latest_common_report_date(*frames: pd.DataFrame) -> date:
    dates = [
        set(item["_报告日期"].dropna().tolist())
        for item in frames
    ]
    common = set.intersection(*dates)
    if not common:
        raise ValueError("三大财务报表没有共同报告期")
    return cast(date, max(common))


def _row_for_date(frame: pd.DataFrame, report_date: date) -> pd.Series:
    rows = frame[frame["_报告日期"] == report_date]
    if rows.empty:
        raise ValueError(f"缺少 {report_date.isoformat()} 报告期")
    return rows.iloc[0]


def _optional_row_for_date(
    frame: pd.DataFrame,
    report_date: date,
) -> pd.Series | None:
    rows = frame[frame["_报告日期"] == report_date]
    return None if rows.empty else rows.iloc[0]


def _latest_share_row(frame: pd.DataFrame, as_of_date: date) -> pd.Series:
    result = frame.copy()
    result["_变动日期"] = pd.to_datetime(
        result["变动日期"], errors="coerce"
    ).dt.date
    result = result[result["_变动日期"] <= as_of_date].sort_values("_变动日期")
    if result.empty:
        raise ValueError("目标日期前没有股本记录")
    return result.iloc[-1]


def _ttm_value(
    frame: pd.DataFrame,
    report_date: date,
    columns: tuple[str, ...],
) -> float:
    current = _value_for_date(frame, report_date, columns)
    if report_date.month == 12 and report_date.day == 31:
        return current
    prior_annual_date = date(report_date.year - 1, 12, 31)
    prior_period_date = report_date.replace(year=report_date.year - 1)
    return (
        current
        + _value_for_date(frame, prior_annual_date, columns)
        - _value_for_date(frame, prior_period_date, columns)
    )


def _optional_nonnegative_ttm_value(
    frame: pd.DataFrame,
    report_date: date,
    columns: tuple[str, ...],
) -> float | None:
    current = _optional_value_for_date(frame, report_date, columns)
    if current is None:
        return None
    if report_date.month == 12 and report_date.day == 31:
        return current if current >= 0 else None
    prior_annual = _optional_value_for_date(
        frame,
        date(report_date.year - 1, 12, 31),
        columns,
    )
    prior_period = _optional_value_for_date(
        frame,
        report_date.replace(year=report_date.year - 1),
        columns,
    )
    if prior_annual is None or prior_period is None:
        return None
    value = current + prior_annual - prior_period
    return value if value >= 0 else None


def _value_for_date(
    frame: pd.DataFrame,
    report_date: date,
    columns: tuple[str, ...],
) -> float:
    return _required_first_number(_row_for_date(frame, report_date), columns)


def _optional_value_for_date(
    frame: pd.DataFrame,
    report_date: date,
    columns: tuple[str, ...],
) -> float | None:
    row = _optional_row_for_date(frame, report_date)
    return None if row is None else _optional_first_number(row, columns)


def _ttm_abstract_value(
    frame: pd.DataFrame,
    report_date: date,
    indicator: str,
) -> float:
    values = _abstract_values(frame, indicator)
    current = _required_mapping_value(values, report_date)
    if report_date.month == 12 and report_date.day == 31:
        return current
    return (
        current
        + _required_mapping_value(values, date(report_date.year - 1, 12, 31))
        - _required_mapping_value(
            values,
            report_date.replace(year=report_date.year - 1),
        )
    )


def _annual_adjusted_earnings(
    abstract: pd.DataFrame,
    balance: pd.DataFrame,
    report_date: date,
) -> list[AnnualEarnings]:
    adjusted_profits = _abstract_values(abstract, "扣非净利润")
    annual_dates = sorted(
        (
            item
            for item in adjusted_profits
            if item.month == 12
            and item.day == 31
            and item <= report_date
        ),
    )[-5:]
    result: list[AnnualEarnings] = []
    for annual_date in annual_dates:
        balance_row = _optional_row_for_date(balance, annual_date)
        if balance_row is None:
            continue
        shares = _optional_number(balance_row, "实收资本(或股本)")
        profit = adjusted_profits.get(annual_date)
        if shares is None or shares <= 0 or profit is None:
            continue
        result.append(
            AnnualEarnings(
                year=annual_date.year,
                adjusted_eps=profit / shares,
            )
        )
    return result


def _abstract_values(frame: pd.DataFrame, indicator: str) -> dict[date, float]:
    rows = frame[frame["指标"] == indicator]
    if rows.empty:
        raise ValueError(f"关键指标缺少 {indicator}")
    row = rows.iloc[0]
    values: dict[date, float] = {}
    for column in frame.columns[2:]:
        parsed = pd.to_datetime(str(column), format="%Y%m%d", errors="coerce")
        value = _number(row[column])
        if pd.notna(parsed) and value is not None:
            values[parsed.date()] = value
    return values


def _required_mapping_value(values: dict[date, float], key: date) -> float:
    value = values.get(key)
    if value is None:
        raise ValueError(f"关键指标缺少 {key.isoformat()} 报告期")
    return value


def _dividend_history(
    frame: pd.DataFrame,
    as_of_date: date,
    total_shares: float,
) -> tuple[list[float], float]:
    result = frame.copy()
    result["_派息日期"] = pd.to_datetime(result["派息日"], errors="coerce")
    result["_每十股派息"] = pd.to_numeric(
        result["派息比例"], errors="coerce"
    )
    paid = result[
        result["_派息日期"].notna()
        & (result["_派息日期"] <= pd.Timestamp(as_of_date))
        & (result["_每十股派息"] > 0)
    ].copy()
    trailing_start = pd.Timestamp(as_of_date - timedelta(days=365))
    trailing = paid[paid["_派息日期"] > trailing_start]
    ttm_total = float(trailing["_每十股派息"].sum()) / 10 * total_shares

    annual: dict[int, float] = {}
    for _, row in paid.iterrows():
        match = re.search(r"(19|20)\d{2}", str(row.get("报告时间", "")))
        if match is None:
            continue
        year = int(match.group(0))
        annual[year] = annual.get(year, 0.0) + float(row["_每十股派息"])
    annual_values = [annual[year] / 10 * total_shares for year in sorted(annual)]
    return annual_values[-5:], ttm_total


def _business_segments(
    frame: pd.DataFrame,
    report_date: date,
) -> list[BusinessSegment]:
    if frame.empty:
        return []
    result = frame.copy()
    result["_报告日期"] = pd.to_datetime(
        result["报告日期"], errors="coerce"
    ).dt.date
    eligible = result[result["_报告日期"] <= report_date]
    if eligible.empty:
        return []
    latest_date = max(eligible["_报告日期"].dropna().tolist())
    eligible = eligible[eligible["_报告日期"] == latest_date]
    products = eligible[eligible["分类类型"] == "按产品分类"]
    selected = products if not products.empty else eligible[
        eligible["分类类型"] == "按行业分类"
    ]
    segments: list[BusinessSegment] = []
    for _, row in selected.iterrows():
        revenue = _optional_number(row, "主营收入")
        cost = _optional_number(row, "主营成本")
        if revenue is None or cost is None:
            continue
        segments.append(
            BusinessSegment(
                name=str(row["主营构成"]),
                revenue=revenue,
                cost=cost,
                disclosed_profit=_optional_number(row, "主营利润"),
            )
        )
    return segments


def _sum_numbers(row: pd.Series, columns: tuple[str, ...]) -> float:
    return sum(_optional_number(row, column) or 0.0 for column in columns)


def _required_first_number(
    row: pd.Series,
    columns: tuple[str, ...],
) -> float:
    value = _optional_first_number(row, columns)
    if value is not None:
        return value
    raise ValueError(f"缺少必需财务字段: {', '.join(columns)}")


def _optional_first_number(
    row: pd.Series,
    columns: tuple[str, ...],
) -> float | None:
    for column in columns:
        value = _optional_number(row, column)
        if value is not None:
            return value
    return None


def _required_number(row: pd.Series, column: str) -> float:
    value = _optional_number(row, column)
    if value is None:
        raise ValueError(f"缺少必需财务字段: {column}")
    return value


def _optional_number(row: pd.Series | None, column: str) -> float | None:
    if row is None or column not in row:
        return None
    return _number(row[column])


def _number(value: object) -> float | None:
    parsed = pd.to_numeric(
        pd.Series([value], dtype="object"),
        errors="coerce",
    ).iloc[0]
    if pd.isna(parsed):
        return None
    return float(parsed)
