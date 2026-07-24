from __future__ import annotations

from datetime import date

import pytest

from fourseasquant.fundamental_mechanical import (
    AnnualEarnings,
    BusinessSegment,
    PersonalFundamentalMonthlyInput,
    ShareholderActions,
    calculate_personal_fundamental_monthly_snapshot,
)


def test_personal_fundamental_monthly_snapshot_calculates_four_pillars() -> None:
    snapshot = calculate_personal_fundamental_monthly_snapshot(
        PersonalFundamentalMonthlyInput(
            code="600000",
            as_of_date=date(2026, 6, 30),
            price=20.0,
            total_shares=100_000_000,
            floating_shares=50_000_000,
            ttm_parent_net_profit=250_000_000,
            ttm_adjusted_parent_net_profit=200_000_000,
            cash_and_equivalents=500_000_000,
            short_term_interest_bearing_debt=100_000_000,
            long_term_interest_bearing_debt=100_000_000,
            parent_equity=1_000_000_000,
            ttm_operating_cash_flow=300_000_000,
            ttm_capital_expenditure=100_000_000,
            ttm_pretax_profit=150_000_000,
            ttm_revenue=1_300_000_000,
            ttm_cash_dividend=40_000_000,
            current_inventory=120_000_000,
            prior_inventory=100_000_000,
            current_revenue=1_300_000_000,
            prior_revenue=1_000_000_000,
            annual_earnings=[
                AnnualEarnings(year=2021, adjusted_eps=1.0),
                AnnualEarnings(year=2022, adjusted_eps=1.2),
                AnnualEarnings(year=2023, adjusted_eps=1.44),
                AnnualEarnings(year=2024, adjusted_eps=1.728),
                AnnualEarnings(year=2025, adjusted_eps=2.0736),
            ],
            annual_cash_dividends=[10, 12, 15, 18, 20],
            historical_adjusted_pe=[8.0, 9.0, 11.0, 12.0],
            peer_adjusted_pe=[7.0, 10.0, 13.0],
            historical_pretax_margins=[0.08, 0.10, 0.12],
            peer_pretax_margins=[-0.10, 150 / 1_300, 0.15],
            business_segments=[
                BusinessSegment(
                    name="业务甲",
                    revenue=600,
                    cost=300,
                    disclosed_profit=50,
                ),
                BusinessSegment(
                    name="业务乙",
                    revenue=800,
                    cost=600,
                    disclosed_profit=80,
                ),
            ],
            institution_holding_ratio=0.12,
            prior_institution_holding_ratio=0.10,
            shareholder_actions=ShareholderActions(
                average_floating_market_cap=1_000_000_000,
                insider_net_purchase_amount=3_000_000,
                cancelled_buyback_amount=10_000_000,
                newly_issued_shares=10_000_000,
                shares_before_issuance=100_000_000,
            ),
            floating_market_cap_universe=[
                500_000_000,
                1_000_000_000,
                2_000_000_000,
            ],
        )
    )

    assert snapshot.adjusted_pe == pytest.approx(10)
    assert snapshot.ordinary_pe == pytest.approx(8)
    assert snapshot.five_year_adjusted_eps_cagr == pytest.approx(0.20)
    assert snapshot.positive_growth_years == 4
    assert snapshot.dividend_yield == pytest.approx(0.02)
    assert snapshot.lynch_growth_value_ratio == pytest.approx(2.2)
    assert snapshot.lynch_growth_value_label == "good"
    assert snapshot.net_cash_per_share == pytest.approx(3)
    assert snapshot.cash_adjusted_price == pytest.approx(17)
    assert snapshot.cash_adjusted_pe == pytest.approx(8.5)
    assert snapshot.debt_to_equity == pytest.approx(0.20)
    assert snapshot.short_term_debt_share == pytest.approx(0.50)
    assert snapshot.dividend_payout_ratio == pytest.approx(0.16)
    assert snapshot.consecutive_dividend_years == 5
    assert snapshot.dividend_continuously_increased is True
    assert snapshot.free_cash_flow_per_share == pytest.approx(2)
    assert snapshot.price_to_free_cash_flow == pytest.approx(10)
    assert snapshot.inventory_growth_minus_revenue_growth == pytest.approx(-0.10)
    assert snapshot.pretax_margin == pytest.approx(150 / 1_300)
    assert snapshot.pretax_margin_historical_percentile == pytest.approx(200 / 3)
    assert snapshot.pretax_margin_peer_percentile == pytest.approx(200 / 3)
    assert snapshot.main_business_name == "业务乙"
    assert snapshot.main_business_profit_share == pytest.approx(80 / 130)
    assert snapshot.institution_holding_change == pytest.approx(0.02)
    assert snapshot.true_money_signal_score == pytest.approx(80)
    assert snapshot.capital_action_signal.insider_net_purchase_amount == 3_000_000
    assert snapshot.capital_action_signal.insider_adjustment == pytest.approx(30)
    assert snapshot.capital_action_signal.cancelled_buyback_amount == 10_000_000
    assert snapshot.capital_action_signal.buyback_bonus == pytest.approx(20)
    assert snapshot.capital_action_signal.dilution_ratio == pytest.approx(0.10)
    assert snapshot.capital_action_signal.dilution_penalty == pytest.approx(20)
    assert snapshot.capital_action_signal.newly_issued_shares == 10_000_000
    assert snapshot.capital_action_signal.shares_before_issuance == 100_000_000
    assert snapshot.inventory_status == "available"
    assert snapshot.floating_market_cap == pytest.approx(1_000_000_000)
    assert snapshot.floating_market_cap_percentile == pytest.approx(200 / 3)


def test_non_positive_earnings_do_not_produce_misleading_valuation_ratios() -> None:
    snapshot = calculate_personal_fundamental_monthly_snapshot(
        PersonalFundamentalMonthlyInput(
            code="600001",
            as_of_date=date(2026, 6, 30),
            price=10,
            total_shares=100,
            floating_shares=50,
            ttm_parent_net_profit=-10,
            ttm_adjusted_parent_net_profit=0,
            cash_and_equivalents=0,
            short_term_interest_bearing_debt=0,
            long_term_interest_bearing_debt=0,
            parent_equity=100,
            ttm_operating_cash_flow=0,
            ttm_capital_expenditure=0,
            ttm_pretax_profit=-10,
            ttm_revenue=100,
            ttm_cash_dividend=0,
            inventory_applicable=False,
            annual_earnings=[
                AnnualEarnings(year=2021, adjusted_eps=-1),
                AnnualEarnings(year=2025, adjusted_eps=1),
            ],
            business_segments=[],
            shareholder_actions=ShareholderActions(
                average_floating_market_cap=500,
            ),
            floating_market_cap_universe=[500],
        )
    )

    assert snapshot.adjusted_pe is None
    assert snapshot.ordinary_pe is None
    assert snapshot.five_year_adjusted_eps_cagr is None
    assert snapshot.lynch_growth_value_ratio is None
    assert snapshot.lynch_growth_value_label == "not_applicable"
    assert snapshot.price_to_free_cash_flow is None
    assert snapshot.main_business_name is None
    assert snapshot.inventory_status == "not_applicable"


def test_five_year_growth_ignores_older_earnings_history() -> None:
    source = PersonalFundamentalMonthlyInput(
        code="600002",
        as_of_date=date(2026, 6, 30),
        price=10,
        total_shares=100,
        floating_shares=50,
        ttm_parent_net_profit=10,
        ttm_adjusted_parent_net_profit=10,
        cash_and_equivalents=0,
        short_term_interest_bearing_debt=0,
        long_term_interest_bearing_debt=0,
        parent_equity=100,
        ttm_operating_cash_flow=10,
        ttm_capital_expenditure=0,
        ttm_pretax_profit=10,
        ttm_revenue=100,
        ttm_cash_dividend=0,
        annual_earnings=[
            AnnualEarnings(year=2020, adjusted_eps=0.01),
            AnnualEarnings(year=2021, adjusted_eps=1.0),
            AnnualEarnings(year=2022, adjusted_eps=1.2),
            AnnualEarnings(year=2023, adjusted_eps=1.44),
            AnnualEarnings(year=2024, adjusted_eps=1.728),
            AnnualEarnings(year=2025, adjusted_eps=2.0736),
        ],
        business_segments=[
            BusinessSegment(
                name="已披露分部利润",
                revenue=60,
                cost=30,
                disclosed_profit=10,
            ),
            BusinessSegment(
                name="缺少分部利润",
                revenue=40,
                cost=20,
            ),
        ],
        shareholder_actions=ShareholderActions(
            average_floating_market_cap=500,
        ),
        floating_market_cap_universe=[500],
    )

    snapshot = calculate_personal_fundamental_monthly_snapshot(source)

    assert snapshot.five_year_adjusted_eps_cagr == pytest.approx(0.20)
    assert snapshot.positive_growth_years == 4
    assert snapshot.main_business_name is None
