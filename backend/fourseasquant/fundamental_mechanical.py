from __future__ import annotations

import math
from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, Field


RULES_VERSION = "personal-fundamental-v1"
GrowthValueLabel = Literal[
    "poor",
    "unclassified",
    "acceptable",
    "good",
    "not_applicable",
]
InventoryStatus = Literal["available", "insufficient_data", "not_applicable"]


class AnnualEarnings(BaseModel):
    year: int
    adjusted_eps: float


class BusinessSegment(BaseModel):
    name: str = Field(min_length=1)
    revenue: float
    cost: float
    disclosed_profit: float | None = None


class ShareholderActions(BaseModel):
    average_floating_market_cap: float = Field(gt=0)
    insider_net_purchase_amount: float = 0
    cancelled_buyback_amount: float = Field(default=0, ge=0)
    newly_issued_shares: float = Field(default=0, ge=0)
    shares_before_issuance: float | None = Field(default=None, gt=0)


class PersonalFundamentalMonthlyInput(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    as_of_date: date
    price: float = Field(gt=0)
    total_shares: float = Field(gt=0)
    floating_shares: float = Field(gt=0)
    ttm_parent_net_profit: float
    ttm_adjusted_parent_net_profit: float
    cash_and_equivalents: float = Field(ge=0)
    short_term_interest_bearing_debt: float = Field(ge=0)
    long_term_interest_bearing_debt: float = Field(ge=0)
    parent_equity: float
    ttm_operating_cash_flow: float
    ttm_capital_expenditure: float | None = Field(default=None, ge=0)
    ttm_pretax_profit: float
    ttm_revenue: float
    ttm_cash_dividend: float = Field(ge=0)
    current_inventory: float | None = Field(default=None, ge=0)
    prior_inventory: float | None = Field(default=None, ge=0)
    inventory_applicable: bool = True
    current_revenue: float | None = None
    prior_revenue: float | None = None
    annual_earnings: list[AnnualEarnings]
    annual_cash_dividends: list[float] = Field(default_factory=list)
    historical_adjusted_pe: list[float] = Field(default_factory=list)
    peer_adjusted_pe: list[float] = Field(default_factory=list)
    historical_pretax_margins: list[float] = Field(default_factory=list)
    peer_pretax_margins: list[float] = Field(default_factory=list)
    business_segments: list[BusinessSegment]
    institution_holding_ratio: float | None = Field(default=None, ge=0, le=1)
    prior_institution_holding_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    shareholder_actions: ShareholderActions | None = None
    floating_market_cap_universe: list[float]


class PersonalFundamentalMonthlySnapshot(BaseModel):
    rules_version: str
    code: str
    as_of_date: date
    ordinary_pe: float | None
    adjusted_pe: float | None
    adjusted_pe_historical_percentile: float | None
    adjusted_pe_peer_percentile: float | None
    five_year_adjusted_eps_cagr: float | None
    positive_growth_years: int
    dividend_yield: float
    lynch_growth_value_ratio: float | None
    lynch_growth_value_label: GrowthValueLabel
    net_cash_per_share: float
    cash_adjusted_price: float
    cash_adjusted_pe: float | None
    short_term_interest_bearing_debt: float
    long_term_interest_bearing_debt: float
    debt_to_equity: float | None
    short_term_debt_share: float | None
    dividend_payout_ratio: float | None
    consecutive_dividend_years: int
    dividend_continuously_increased: bool | None
    free_cash_flow_per_share: float | None
    price_to_free_cash_flow: float | None
    inventory_status: InventoryStatus
    inventory_growth: float | None
    revenue_growth: float | None
    inventory_growth_minus_revenue_growth: float | None
    pretax_margin: float | None
    pretax_margin_historical_percentile: float | None
    pretax_margin_peer_percentile: float | None
    main_business_name: str | None
    main_business_profit_share: float | None
    institution_holding_ratio: float | None
    institution_holding_change: float | None
    capital_action_signal: CapitalActionSignal | None
    true_money_signal_score: float | None
    floating_market_cap: float
    floating_market_cap_percentile: float | None


class CapitalActionSignal(BaseModel):
    insider_net_purchase_amount: float
    insider_net_purchase_ratio: float
    insider_adjustment: float
    cancelled_buyback_amount: float
    cancelled_buyback_ratio: float
    buyback_bonus: float
    dilution_ratio: float
    dilution_penalty: float
    newly_issued_shares: float
    shares_before_issuance: float | None


def calculate_personal_fundamental_monthly_snapshot(
    source: PersonalFundamentalMonthlyInput,
) -> PersonalFundamentalMonthlySnapshot:
    market_cap = source.price * source.total_shares
    floating_market_cap = source.price * source.floating_shares
    ordinary_pe = _ratio_when_denominator_positive(
        market_cap,
        source.ttm_parent_net_profit,
    )
    adjusted_pe = _ratio_when_denominator_positive(
        market_cap,
        source.ttm_adjusted_parent_net_profit,
    )
    earnings_growth = _annual_eps_cagr(source.annual_earnings)
    dividend_yield = source.ttm_cash_dividend / market_cap
    growth_value_ratio = _growth_value_ratio(
        earnings_growth,
        dividend_yield,
        adjusted_pe,
    )
    net_cash = (
        source.cash_and_equivalents
        - source.short_term_interest_bearing_debt
        - source.long_term_interest_bearing_debt
    )
    net_cash_per_share = net_cash / source.total_shares
    cash_adjusted_price = source.price - net_cash_per_share
    cash_adjusted_market_cap = market_cap - net_cash
    cash_adjusted_pe = _ratio_when_denominator_positive(
        cash_adjusted_market_cap,
        source.ttm_adjusted_parent_net_profit,
    )
    interest_bearing_debt = (
        source.short_term_interest_bearing_debt
        + source.long_term_interest_bearing_debt
    )
    debt_to_equity = _ratio_when_denominator_positive(
        interest_bearing_debt,
        source.parent_equity,
    )
    short_term_debt_share = _ratio_when_denominator_positive(
        source.short_term_interest_bearing_debt,
        interest_bearing_debt,
    )
    free_cash_flow = (
        source.ttm_operating_cash_flow - source.ttm_capital_expenditure
        if source.ttm_capital_expenditure is not None
        else None
    )
    main_business_name, main_business_profit_share = _main_business(
        source.business_segments
    )
    institution_holding_change = _optional_difference(
        source.institution_holding_ratio,
        source.prior_institution_holding_ratio,
    )
    inventory_status, inventory_growth, revenue_growth = (
        _inventory_growth_result(source)
    )
    pretax_margin = _ratio_when_denominator_positive(
        source.ttm_pretax_profit,
        source.ttm_revenue,
    )
    capital_action_signal = (
        _capital_action_signal(source.shareholder_actions)
        if source.shareholder_actions is not None
        else None
    )

    return PersonalFundamentalMonthlySnapshot(
        rules_version=RULES_VERSION,
        code=source.code,
        as_of_date=source.as_of_date,
        ordinary_pe=ordinary_pe,
        adjusted_pe=adjusted_pe,
        adjusted_pe_historical_percentile=_positive_percentile(
            adjusted_pe,
            source.historical_adjusted_pe,
        ),
        adjusted_pe_peer_percentile=_positive_percentile(
            adjusted_pe,
            source.peer_adjusted_pe,
        ),
        five_year_adjusted_eps_cagr=earnings_growth,
        positive_growth_years=_positive_growth_years(source.annual_earnings),
        dividend_yield=dividend_yield,
        lynch_growth_value_ratio=growth_value_ratio,
        lynch_growth_value_label=_growth_value_label(growth_value_ratio),
        net_cash_per_share=net_cash_per_share,
        cash_adjusted_price=cash_adjusted_price,
        cash_adjusted_pe=cash_adjusted_pe,
        short_term_interest_bearing_debt=source.short_term_interest_bearing_debt,
        long_term_interest_bearing_debt=source.long_term_interest_bearing_debt,
        debt_to_equity=debt_to_equity,
        short_term_debt_share=short_term_debt_share,
        dividend_payout_ratio=_ratio_when_denominator_positive(
            source.ttm_cash_dividend,
            source.ttm_parent_net_profit,
        ),
        consecutive_dividend_years=_trailing_positive_count(
            source.annual_cash_dividends
        ),
        dividend_continuously_increased=_dividend_continuously_increased(
            source.annual_cash_dividends
        ),
        free_cash_flow_per_share=(
            free_cash_flow / source.total_shares
            if free_cash_flow is not None
            else None
        ),
        price_to_free_cash_flow=(
            _ratio_when_denominator_positive(market_cap, free_cash_flow)
            if free_cash_flow is not None
            else None
        ),
        inventory_status=inventory_status,
        inventory_growth=inventory_growth,
        revenue_growth=revenue_growth,
        inventory_growth_minus_revenue_growth=(
            inventory_growth - revenue_growth
            if inventory_growth is not None and revenue_growth is not None
            else None
        ),
        pretax_margin=pretax_margin,
        pretax_margin_historical_percentile=_numeric_percentile(
            pretax_margin,
            source.historical_pretax_margins,
        ),
        pretax_margin_peer_percentile=_numeric_percentile(
            pretax_margin,
            source.peer_pretax_margins,
        ),
        main_business_name=main_business_name,
        main_business_profit_share=main_business_profit_share,
        institution_holding_ratio=source.institution_holding_ratio,
        institution_holding_change=institution_holding_change,
        capital_action_signal=capital_action_signal,
        true_money_signal_score=(
            capital_action_signal_score(capital_action_signal)
            if capital_action_signal is not None
            else None
        ),
        floating_market_cap=floating_market_cap,
        floating_market_cap_percentile=_positive_percentile(
            floating_market_cap,
            source.floating_market_cap_universe,
        ),
    )


def _ratio_when_denominator_positive(
    numerator: float,
    denominator: float,
) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _annual_eps_cagr(history: list[AnnualEarnings]) -> float | None:
    ordered = sorted(history, key=lambda item: item.year)[-5:]
    if len(ordered) < 2:
        return None
    first = ordered[0]
    last = ordered[-1]
    years = last.year - first.year
    if years <= 0 or first.adjusted_eps <= 0 or last.adjusted_eps <= 0:
        return None
    return math.pow(last.adjusted_eps / first.adjusted_eps, 1 / years) - 1


def _positive_growth_years(history: list[AnnualEarnings]) -> int:
    ordered = sorted(history, key=lambda item: item.year)[-5:]
    return sum(
        current.adjusted_eps > previous.adjusted_eps
        for previous, current in zip(ordered, ordered[1:], strict=False)
    )


def _growth_value_ratio(
    earnings_growth: float | None,
    dividend_yield: float,
    adjusted_pe: float | None,
) -> float | None:
    if earnings_growth is None or adjusted_pe is None:
        return None
    return (earnings_growth * 100 + dividend_yield * 100) / adjusted_pe


def _growth_value_label(value: float | None) -> GrowthValueLabel:
    if value is None:
        return "not_applicable"
    if value < 1:
        return "poor"
    if value >= 2:
        return "good"
    if value >= 1.5:
        return "acceptable"
    return "unclassified"


def _trailing_positive_count(values: list[float]) -> int:
    count = 0
    for value in reversed(values):
        if value <= 0:
            break
        count += 1
    return count


def _dividend_continuously_increased(
    values: list[float],
) -> bool | None:
    trailing_count = _trailing_positive_count(values)
    if trailing_count < 2:
        return None
    trailing = values[-trailing_count:]
    return all(
        current > previous
        for previous, current in zip(trailing, trailing[1:], strict=False)
    )


def _inventory_growth_result(
    source: PersonalFundamentalMonthlyInput,
) -> tuple[InventoryStatus, float | None, float | None]:
    if not source.inventory_applicable:
        return "not_applicable", None, None
    current_inventory = source.current_inventory
    prior_inventory = source.prior_inventory
    current_revenue = source.current_revenue
    prior_revenue = source.prior_revenue
    if (
        current_inventory is None
        or prior_inventory is None
        or current_revenue is None
        or prior_revenue is None
    ):
        return "insufficient_data", None, None
    if prior_inventory <= 0 or prior_revenue <= 0:
        return "insufficient_data", None, None
    return (
        "available",
        current_inventory / prior_inventory - 1,
        current_revenue / prior_revenue - 1,
    )


def _main_business(
    segments: list[BusinessSegment],
) -> tuple[str | None, float | None]:
    if not segments:
        return None, None
    any_disclosed_profit = any(
        segment.disclosed_profit is not None for segment in segments
    )
    use_disclosed_profit = all(
        segment.disclosed_profit is not None for segment in segments
    )
    if any_disclosed_profit and not use_disclosed_profit:
        return None, None
    profits = [
        (
            segment,
            (
                cast(float, segment.disclosed_profit)
                if use_disclosed_profit
                else segment.revenue - segment.cost
            ),
        )
        for segment in segments
    ]
    total_profit = sum(value for _, value in profits)
    if total_profit <= 0:
        return None, None
    segment, profit = max(profits, key=lambda item: item[1])
    return segment.name, profit / total_profit


def _optional_difference(
    current: float | None,
    prior: float | None,
) -> float | None:
    if current is None or prior is None:
        return None
    return current - prior


def _capital_action_signal(actions: ShareholderActions) -> CapitalActionSignal:
    insider_ratio = (
        actions.insider_net_purchase_amount
        / actions.average_floating_market_cap
    )
    insider_adjustment = max(-30.0, min(30.0, insider_ratio / 0.003 * 30))
    buyback_ratio = (
        actions.cancelled_buyback_amount
        / actions.average_floating_market_cap
    )
    buyback_bonus = min(20.0, buyback_ratio / 0.01 * 20)
    dilution_ratio = 0.0
    if actions.shares_before_issuance is not None:
        dilution_ratio = (
            actions.newly_issued_shares / actions.shares_before_issuance
        )
    dilution_penalty = min(20.0, dilution_ratio / 0.10 * 20)
    return CapitalActionSignal(
        insider_net_purchase_amount=actions.insider_net_purchase_amount,
        insider_net_purchase_ratio=insider_ratio,
        insider_adjustment=insider_adjustment,
        cancelled_buyback_amount=actions.cancelled_buyback_amount,
        cancelled_buyback_ratio=buyback_ratio,
        buyback_bonus=buyback_bonus,
        dilution_ratio=dilution_ratio,
        dilution_penalty=dilution_penalty,
        newly_issued_shares=actions.newly_issued_shares,
        shares_before_issuance=actions.shares_before_issuance,
    )


def capital_action_signal_score(signal: CapitalActionSignal) -> float:
    return max(
        0.0,
        min(
            100.0,
            50
            + signal.insider_adjustment
            + signal.buyback_bonus
            - signal.dilution_penalty,
        ),
    )


def _positive_percentile(
    value: float | None,
    universe: list[float],
) -> float | None:
    valid = [item for item in universe if item > 0]
    if value is None or not valid:
        return None
    return sum(item <= value for item in valid) / len(valid) * 100


def _numeric_percentile(
    value: float | None,
    universe: list[float],
) -> float | None:
    if value is None or not universe:
        return None
    return sum(item <= value for item in universe) / len(universe) * 100
