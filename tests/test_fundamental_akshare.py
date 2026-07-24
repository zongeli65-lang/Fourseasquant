from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fourseasquant.fundamental_akshare import (
    AKShareFundamentalFrames,
    build_monthly_input_from_akshare,
)


def test_build_monthly_input_maps_units_and_calculates_ttm() -> None:
    report_dates = ["20260331", "20251231", "20250331"]
    announcements = ["20260430", "20260331", "20250430"]
    balance = pd.DataFrame(
        {
            "报告日": report_dates,
            "公告日期": announcements,
            "货币资金": [500.0, 450.0, 400.0],
            "短期借款": [20.0, 15.0, 10.0],
            "一年内到期的非流动负债": [5.0, 4.0, 3.0],
            "长期借款": [30.0, 25.0, 20.0],
            "归属于母公司股东权益合计": [1_000.0, 900.0, 800.0],
            "存货": [120.0, 110.0, 100.0],
            "实收资本(或股本)": [200.0, 200.0, 200.0],
        }
    )
    profit = pd.DataFrame(
        {
            "报告日": report_dates,
            "公告日期": announcements,
            "归属于母公司所有者的净利润": [30.0, 100.0, 20.0],
            "营业收入": [300.0, 1_000.0, 200.0],
            "利润总额": [40.0, 130.0, 25.0],
        }
    )
    cash_flow = pd.DataFrame(
        {
            "报告日": report_dates,
            "公告日期": announcements,
            "经营活动产生的现金流量净额": [50.0, 180.0, 30.0],
            "购建固定资产、无形资产和其他长期资产所支付的现金": [
                10.0,
                40.0,
                8.0,
            ],
        }
    )
    abstract = pd.DataFrame(
        {
            "序号": [1],
            "指标": ["扣非净利润"],
            "20260331": [28.0],
            "20251231": [95.0],
            "20250331": [18.0],
        }
    )
    shares = pd.DataFrame(
        {
            "变动日期": ["2025-01-01"],
            "总股本": [20.0],
            "人民币普通股": [15.0],
        }
    )
    dividends = pd.DataFrame(
        {
            "派息日": ["2026-06-01"],
            "派息比例": [2.0],
            "报告时间": ["2025年报"],
        }
    )
    segments = pd.DataFrame(
        {
            "报告日期": ["2025-12-31"],
            "分类类型": ["按产品分类"],
            "主营构成": ["核心产品"],
            "主营收入": [700.0],
            "主营成本": [300.0],
            "主营利润": [400.0],
        }
    )

    source = build_monthly_input_from_akshare(
        code="600000",
        as_of_date=date(2026, 7, 24),
        price=10.0,
        frames=AKShareFundamentalFrames(
            abstract=abstract,
            balance_sheet=balance,
            profit_sheet=profit,
            cash_flow_sheet=cash_flow,
            share_changes=shares,
            dividends=dividends,
            business_segments=segments,
        ),
    )

    assert source.total_shares == 200_000
    assert source.floating_shares == 150_000
    assert source.ttm_parent_net_profit == pytest.approx(110.0)
    assert source.ttm_adjusted_parent_net_profit == pytest.approx(105.0)
    assert source.ttm_revenue == pytest.approx(1_100.0)
    assert source.ttm_pretax_profit == pytest.approx(145.0)
    assert source.ttm_operating_cash_flow == pytest.approx(200.0)
    assert source.ttm_capital_expenditure == pytest.approx(42.0)
    assert source.ttm_cash_dividend == pytest.approx(40_000.0)
    assert source.current_inventory == 120.0
    assert source.prior_inventory == 100.0
    assert source.shareholder_actions is None
    assert source.business_segments[0].name == "核心产品"
