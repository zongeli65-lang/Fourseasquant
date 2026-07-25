from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fourseasquant.fundamental_lynch_akshare import (
    build_lynch_financial_base_from_akshare,
)


def financial_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2026-03-31",
                "REPORT_TYPE": "一季报",
                "NOTICE_DATE": "2026-04-30",
                "EPSKCJB": 0.52,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2025-12-31",
                "REPORT_TYPE": "年报",
                "NOTICE_DATE": "2026-03-31",
                "EPSKCJB": 1.53,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2025-03-31",
                "REPORT_TYPE": "一季报",
                "NOTICE_DATE": "2025-04-30",
                "EPSKCJB": 0.42,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2024-12-31",
                "REPORT_TYPE": "年报",
                "NOTICE_DATE": "2025-03-29",
                "EPSKCJB": 1.33,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2024-03-31",
                "REPORT_TYPE": "一季报",
                "NOTICE_DATE": "2024-04-30",
                "EPSKCJB": 0.56,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2023-12-31",
                "REPORT_TYPE": "年报",
                "NOTICE_DATE": "2024-04-30",
                "EPSKCJB": 0.97,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2022-12-31",
                "REPORT_TYPE": "年报",
                "NOTICE_DATE": "2023-04-30",
                "EPSKCJB": 0.90,
            },
            {
                "SECURITY_CODE": "600000",
                "SECURITY_NAME_ABBR": "浦发银行",
                "REPORT_DATE": "2026-06-30",
                "REPORT_TYPE": "中报",
                "NOTICE_DATE": "2026-08-30",
                "EPSKCJB": 1.20,
            },
        ]
    )


def test_builds_three_year_and_ttm_values_without_future_disclosures() -> None:
    dividends = pd.DataFrame(
        [
            {"派息日": "2025-08-01", "派息比例": 2.0},
            {"派息日": "2026-06-01", "派息比例": 1.5},
            {"派息日": "2024-06-01", "派息比例": 9.0},
        ]
    )

    result = build_lynch_financial_base_from_akshare(
        code="600000",
        as_of_date=date(2026, 7, 24),
        financial=financial_frame(),
        dividends=dividends,
        audit_status="standard_unqualified",
        performance_forecast_blocked=False,
        major_risk_blocked=False,
    )

    assert [item.year for item in result.annual_adjusted_eps] == [
        2023,
        2024,
        2025,
    ]
    assert [item.value for item in result.annual_adjusted_eps] == [
        0.97,
        1.33,
        1.53,
    ]
    assert result.financial_as_of == date(2026, 3, 31)
    assert result.latest_notice_date == date(2026, 4, 30)
    assert result.ttm_adjusted_eps == pytest.approx(1.63)
    assert result.prior_ttm_adjusted_eps == pytest.approx(1.19)
    assert result.ttm_dividend_per_share == pytest.approx(0.35)


def test_missing_required_financial_columns_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="缺少字段"):
        build_lynch_financial_base_from_akshare(
            code="600000",
            as_of_date=date(2026, 7, 24),
            financial=pd.DataFrame({"SECURITY_CODE": ["600000"]}),
            dividends=pd.DataFrame(columns=["派息日", "派息比例"]),
            audit_status="unknown",
            performance_forecast_blocked=False,
            major_risk_blocked=False,
        )


def test_invalid_dividend_frame_is_not_silently_treated_as_zero() -> None:
    with pytest.raises(ValueError, match="分红"):
        build_lynch_financial_base_from_akshare(
            code="600000",
            as_of_date=date(2026, 7, 24),
            financial=financial_frame(),
            dividends=pd.DataFrame(),
            audit_status="unknown",
            performance_forecast_blocked=False,
            major_risk_blocked=False,
        )


def test_bonus_only_row_without_cash_dividend_is_valid_zero() -> None:
    result = build_lynch_financial_base_from_akshare(
        code="600000",
        as_of_date=date(2026, 7, 24),
        financial=financial_frame(),
        dividends=pd.DataFrame(
            [{"派息日": "2026-06-20", "派息比例": None}]
        ),
        audit_status="unknown",
        performance_forecast_blocked=False,
        major_risk_blocked=False,
    )

    assert result.ttm_dividend_per_share == 0
