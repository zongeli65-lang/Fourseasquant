from __future__ import annotations

from datetime import date

import pytest

from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    AuditStatus,
    LynchFinancialBase,
    calculate_lynch_daily_result,
    rank_lynch_results,
)


def base(
    code: str = "600000",
    *,
    annual: tuple[float, float, float] = (1.0, 1.2, 1.44),
    ttm: float = 1.5,
    prior_ttm: float = 1.3,
    audit_status: AuditStatus = "standard_unqualified",
    performance_forecast_blocked: bool = False,
    major_risk_blocked: bool = False,
) -> LynchFinancialBase:
    return LynchFinancialBase(
        code=code,
        name=f"股票{code}",
        financial_as_of=date(2026, 3, 31),
        latest_notice_date=date(2026, 4, 30),
        annual_adjusted_eps=[
            AnnualAdjustedEps(year=2023 + index, value=value)
            for index, value in enumerate(annual)
        ],
        ttm_adjusted_eps=ttm,
        prior_ttm_adjusted_eps=prior_ttm,
        ttm_dividend_per_share=0.3,
        audit_status=audit_status,
        performance_forecast_blocked=performance_forecast_blocked,
        major_risk_blocked=major_risk_blocked,
        source_urls=["https://example.test/financial"],
    )


def test_three_positive_consecutive_years_produce_absolute_grade() -> None:
    result = calculate_lynch_daily_result(
        base(),
        target_date=date(2026, 7, 24),
        close=10.0,
    )

    assert result.calculable is True
    assert result.three_year_cagr == pytest.approx(0.2)
    assert result.adjusted_pe == pytest.approx(10.0 / 1.5)
    assert result.lynch_ratio == pytest.approx(3.45)
    assert result.absolute_grade == "exceptional"
    assert result.ranking_eligible is True


def test_missing_nonconsecutive_or_nonpositive_years_are_not_calculable() -> None:
    missing_year = base().model_copy(
        update={
            "annual_adjusted_eps": [
                AnnualAdjustedEps(year=2022, value=1.0),
                AnnualAdjustedEps(year=2024, value=1.2),
                AnnualAdjustedEps(year=2025, value=1.4),
            ]
        }
    )

    nonconsecutive = calculate_lynch_daily_result(
        missing_year,
        target_date=date(2026, 7, 24),
        close=10.0,
    )
    nonpositive = calculate_lynch_daily_result(
        base(annual=(1.0, -0.1, 1.4)),
        target_date=date(2026, 7, 24),
        close=10.0,
    )
    ttm_loss = calculate_lynch_daily_result(
        base(ttm=-0.1),
        target_date=date(2026, 7, 24),
        close=10.0,
    )

    assert nonconsecutive.unavailable_reason == "annual_history_not_consecutive"
    assert nonpositive.unavailable_reason == "annual_earnings_not_all_positive"
    assert ttm_loss.unavailable_reason == "ttm_adjusted_earnings_not_positive"
    assert nonconsecutive.ranking_eligible is False
    assert nonpositive.ranking_eligible is False
    assert ttm_loss.ranking_eligible is False


def test_low_base_and_earnings_deterioration_warnings_do_not_change_grade() -> None:
    result = calculate_lynch_daily_result(
        base(
            annual=(0.1, 0.22, 0.4),
            ttm=0.25,
            prior_ttm=0.6,
        ),
        target_date=date(2026, 7, 24),
        close=10.0,
    )

    assert "low_base_or_abnormal_growth" in result.warnings
    assert "annual_growth_above_100_percent" in result.warnings
    assert "ttm_earnings_deteriorated_above_50_percent" in result.warnings
    assert result.absolute_grade == "exceptional"
    assert result.ranking_eligible is True


def test_annual_declines_use_confirmed_warning_thresholds() -> None:
    mild = calculate_lynch_daily_result(
        base(annual=(1.0, 0.9, 1.2)),
        target_date=date(2026, 7, 24),
        close=10.0,
    )
    material = calculate_lynch_daily_result(
        base(annual=(1.0, 0.65, 1.2)),
        target_date=date(2026, 7, 24),
        close=10.0,
    )
    severe = calculate_lynch_daily_result(
        base(annual=(1.0, 0.4, 1.2)),
        target_date=date(2026, 7, 24),
        close=10.0,
    )

    assert "annual_growth_not_continuous" in mild.warnings
    assert "annual_earnings_deteriorated_above_30_percent" in material.warnings
    assert "annual_earnings_deteriorated_above_50_percent" in severe.warnings


def test_audit_forecast_and_major_risk_gate_ranking_but_keep_value() -> None:
    results = [
        calculate_lynch_daily_result(
            base("600001", audit_status="unknown"),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600002", performance_forecast_blocked=True),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600003", major_risk_blocked=True),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
    ]

    assert all(item.calculable for item in results)
    assert all(not item.ranking_eligible for item in results)
    assert [item.ranking_exclusion_reason for item in results] == [
        "audit_not_standard_unqualified",
        "performance_forecast_risk",
        "major_public_risk",
    ]


def test_percentile_uses_only_eligible_calculable_results_and_averages_ties() -> None:
    results = [
        calculate_lynch_daily_result(
            base("600001", annual=(1.0, 1.1, 1.21)),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600002", annual=(1.0, 1.2, 1.44)),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600003", annual=(1.0, 1.2, 1.44)),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600004", annual=(1.0, 1.4, 1.96)),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
        calculate_lynch_daily_result(
            base("600005", audit_status="unknown"),
            target_date=date(2026, 7, 24),
            close=10.0,
        ),
    ]

    ranked = {item.code: item for item in rank_lynch_results(results)}

    assert ranked["600001"].market_percentile == 0.0
    assert ranked["600002"].market_percentile == 50.0
    assert ranked["600003"].market_percentile == 50.0
    assert ranked["600004"].market_percentile == 100.0
    assert ranked["600005"].market_percentile is None
    assert all(item.percentile_universe_size == 4 for item in ranked.values())
