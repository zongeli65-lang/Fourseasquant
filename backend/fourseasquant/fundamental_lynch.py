from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


LYNCH_RULES_VERSION = "lynch-core-v2"
AuditStatus = Literal[
    "standard_unqualified",
    "emphasis_of_matter",
    "qualified",
    "adverse",
    "disclaimer",
    "going_concern_uncertainty",
    "unknown",
]
LynchAbsoluteGrade = Literal[
    "exceptional",
    "excellent",
    "reasonable",
    "weak",
    "earnings_contraction",
    "insufficient_data",
]
LynchUnavailableReason = Literal[
    "financial_data_unavailable",
    "annual_history_not_exactly_three_years",
    "annual_history_not_consecutive",
    "annual_earnings_not_all_positive",
    "ttm_adjusted_earnings_not_positive",
    "close_not_positive",
]
LynchRankingExclusionReason = Literal[
    "not_calculable",
    "audit_not_standard_unqualified",
    "performance_forecast_risk",
    "major_public_risk",
]
LynchWarning = Literal[
    "low_base_or_abnormal_growth",
    "annual_growth_above_100_percent",
    "annual_growth_not_continuous",
    "annual_earnings_deteriorated_above_30_percent",
    "annual_earnings_deteriorated_above_50_percent",
    "ttm_earnings_deteriorated_above_30_percent",
    "ttm_earnings_deteriorated_above_50_percent",
]
GrowthStatus = Literal[
    "continuous_growth",
    "non_continuous_growth",
    "earnings_contraction",
    "loss_or_nonpositive",
    "insufficient_data",
]
ValuationStatus = Literal[
    "applicable",
    "loss_making",
    "invalid_price",
    "insufficient_data",
]
FinancialSafetyStatus = Literal["available", "invalid_equity", "insufficient_data"]
CoreDataStatus = Literal[
    "complete",
    "economic_not_applicable",
    "source_missing",
]


class AnnualAdjustedEps(BaseModel):
    year: int = Field(ge=1990, le=2200)
    value: float


class LynchFinancialBase(BaseModel):
    rules_version: str = LYNCH_RULES_VERSION
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    financial_as_of: date | None
    latest_notice_date: date | None
    annual_adjusted_eps: list[AnnualAdjustedEps]
    ttm_adjusted_eps: float | None
    prior_ttm_adjusted_eps: float | None
    ttm_dividend_per_share: float = Field(ge=0)
    net_debt_to_equity: float | None = None
    financial_safety_status: FinancialSafetyStatus = "insufficient_data"
    audit_status: AuditStatus = "unknown"
    performance_forecast_blocked: bool = False
    major_risk_blocked: bool = False
    risk_reasons: list[str] = Field(default_factory=list)
    financial_unavailable_reason: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class LynchDailyResult(BaseModel):
    rules_version: str = LYNCH_RULES_VERSION
    target_date: date
    code: str
    name: str
    close: float
    financial_as_of: date | None
    latest_notice_date: date | None
    annual_adjusted_eps: list[AnnualAdjustedEps]
    ttm_adjusted_eps: float | None
    prior_ttm_adjusted_eps: float | None
    ttm_dividend_per_share: float
    net_debt_to_equity: float | None
    financial_safety_status: FinancialSafetyStatus
    three_year_cagr: float | None
    growth_status: GrowthStatus
    dividend_yield: float | None
    adjusted_pe: float | None
    valuation_status: ValuationStatus
    lynch_ratio: float | None
    absolute_grade: LynchAbsoluteGrade
    warnings: list[LynchWarning]
    calculable: bool
    unavailable_reason: LynchUnavailableReason | None
    audit_status: AuditStatus
    performance_forecast_blocked: bool
    major_risk_blocked: bool
    risk_reasons: list[str]
    ranking_eligible: bool
    ranking_exclusion_reason: LynchRankingExclusionReason | None
    market_percentile: float | None = None
    percentile_universe_size: int = 0
    core_data_status: CoreDataStatus


def calculate_lynch_daily_result(
    financial: LynchFinancialBase,
    *,
    target_date: date,
    close: float,
) -> LynchDailyResult:
    annual = sorted(financial.annual_adjusted_eps, key=lambda item: item.year)
    unavailable = _unavailable_reason(
        annual,
        financial.ttm_adjusted_eps,
        close,
        financial_unavailable=financial.financial_unavailable_reason is not None,
    )
    calculable = unavailable is None
    cagr: float | None = None
    dividend_yield: float | None = None
    adjusted_pe: float | None = None
    ratio: float | None = None
    if calculable:
        cagr = math.sqrt(annual[-1].value / annual[0].value) - 1
        dividend_yield = financial.ttm_dividend_per_share / close
        assert financial.ttm_adjusted_eps is not None
        adjusted_pe = close / financial.ttm_adjusted_eps
        ratio = (cagr * 100 + dividend_yield * 100) / adjusted_pe

    warnings = _warnings(
        annual=annual,
        cagr=cagr,
        ttm_adjusted_eps=financial.ttm_adjusted_eps,
        prior_ttm_adjusted_eps=financial.prior_ttm_adjusted_eps,
    )
    exclusion = _ranking_exclusion_reason(financial, calculable=calculable)
    growth_status = _growth_status(annual)
    valuation_status = _valuation_status(
        financial.ttm_adjusted_eps,
        close,
    )
    core_data_status = _core_data_status(
        annual=annual,
        ttm_adjusted_eps=financial.ttm_adjusted_eps,
        close=close,
        financial_safety_status=financial.financial_safety_status,
    )
    return LynchDailyResult(
        target_date=target_date,
        code=financial.code,
        name=financial.name,
        close=close,
        financial_as_of=financial.financial_as_of,
        latest_notice_date=financial.latest_notice_date,
        annual_adjusted_eps=annual,
        ttm_adjusted_eps=financial.ttm_adjusted_eps,
        prior_ttm_adjusted_eps=financial.prior_ttm_adjusted_eps,
        ttm_dividend_per_share=financial.ttm_dividend_per_share,
        net_debt_to_equity=financial.net_debt_to_equity,
        financial_safety_status=financial.financial_safety_status,
        three_year_cagr=cagr,
        growth_status=growth_status,
        dividend_yield=dividend_yield,
        adjusted_pe=adjusted_pe,
        valuation_status=valuation_status,
        lynch_ratio=ratio,
        absolute_grade=_absolute_grade(ratio),
        warnings=warnings,
        calculable=calculable,
        unavailable_reason=unavailable,
        audit_status=financial.audit_status,
        performance_forecast_blocked=financial.performance_forecast_blocked,
        major_risk_blocked=financial.major_risk_blocked,
        risk_reasons=list(financial.risk_reasons),
        ranking_eligible=exclusion is None,
        ranking_exclusion_reason=exclusion,
        core_data_status=core_data_status,
    )


def rank_lynch_results(
    results: list[LynchDailyResult],
) -> list[LynchDailyResult]:
    eligible = [
        item
        for item in results
        if item.ranking_eligible and item.lynch_ratio is not None
    ]
    universe_size = len(eligible)
    indexes_by_value: dict[float, list[int]] = defaultdict(list)
    for index, item in enumerate(
        sorted(eligible, key=lambda candidate: candidate.lynch_ratio or 0)
    ):
        assert item.lynch_ratio is not None
        indexes_by_value[item.lynch_ratio].append(index)
    percentile_by_value: dict[float, float] = {}
    for value, indexes in indexes_by_value.items():
        average_index = sum(indexes) / len(indexes)
        percentile_by_value[value] = (
            100.0
            if universe_size == 1
            else average_index / (universe_size - 1) * 100
        )
    return [
        item.model_copy(
            update={
                "market_percentile": (
                    percentile_by_value[item.lynch_ratio]
                    if item.ranking_eligible and item.lynch_ratio is not None
                    else None
                ),
                "percentile_universe_size": universe_size,
            }
        )
        for item in results
    ]


def _unavailable_reason(
    annual: list[AnnualAdjustedEps],
    ttm_adjusted_eps: float | None,
    close: float,
    financial_unavailable: bool,
) -> LynchUnavailableReason | None:
    if financial_unavailable:
        return "financial_data_unavailable"
    if len(annual) != 3:
        return "annual_history_not_exactly_three_years"
    if [item.year for item in annual] != list(
        range(annual[0].year, annual[0].year + 3)
    ):
        return "annual_history_not_consecutive"
    if any(item.value <= 0 for item in annual):
        return "annual_earnings_not_all_positive"
    if ttm_adjusted_eps is None or ttm_adjusted_eps <= 0:
        return "ttm_adjusted_earnings_not_positive"
    if close <= 0:
        return "close_not_positive"
    return None


def _warnings(
    *,
    annual: list[AnnualAdjustedEps],
    cagr: float | None,
    ttm_adjusted_eps: float | None,
    prior_ttm_adjusted_eps: float | None,
) -> list[LynchWarning]:
    result: list[LynchWarning] = []
    annual_changes = [
        current.value / previous.value - 1
        for previous, current in zip(annual, annual[1:], strict=False)
        if previous.value > 0
    ]
    if cagr is not None and (
        cagr >= 0.5 or any(change >= 1.0 for change in annual_changes)
    ):
        result.append("low_base_or_abnormal_growth")
    if any(change >= 1.0 for change in annual_changes):
        result.append("annual_growth_above_100_percent")
    minimum_annual_change = min(annual_changes, default=0.0)
    if minimum_annual_change <= -0.5:
        result.append("annual_earnings_deteriorated_above_50_percent")
    elif minimum_annual_change <= -0.3:
        result.append("annual_earnings_deteriorated_above_30_percent")
    elif minimum_annual_change < 0:
        result.append("annual_growth_not_continuous")

    if (
        ttm_adjusted_eps is not None
        and prior_ttm_adjusted_eps is not None
        and prior_ttm_adjusted_eps > 0
    ):
        ttm_change = ttm_adjusted_eps / prior_ttm_adjusted_eps - 1
        if ttm_change <= -0.5:
            result.append("ttm_earnings_deteriorated_above_50_percent")
        elif ttm_change <= -0.3:
            result.append("ttm_earnings_deteriorated_above_30_percent")
    return result


def _absolute_grade(ratio: float | None) -> LynchAbsoluteGrade:
    if ratio is None:
        return "insufficient_data"
    if ratio >= 2:
        return "exceptional"
    if ratio >= 1.5:
        return "excellent"
    if ratio >= 1:
        return "reasonable"
    if ratio >= 0:
        return "weak"
    return "earnings_contraction"


def _growth_status(annual: list[AnnualAdjustedEps]) -> GrowthStatus:
    if len(annual) != 3 or [
        item.year for item in annual
    ] != list(range(annual[0].year, annual[0].year + 3)):
        return "insufficient_data"
    if any(item.value <= 0 for item in annual):
        return "loss_or_nonpositive"
    changes = [
        current.value - previous.value
        for previous, current in zip(annual, annual[1:], strict=False)
    ]
    if all(change > 0 for change in changes):
        return "continuous_growth"
    if annual[-1].value < annual[0].value:
        return "earnings_contraction"
    return "non_continuous_growth"


def _valuation_status(
    ttm_adjusted_eps: float | None,
    close: float,
) -> ValuationStatus:
    if close <= 0:
        return "invalid_price"
    if ttm_adjusted_eps is None:
        return "insufficient_data"
    if ttm_adjusted_eps <= 0:
        return "loss_making"
    return "applicable"


def _core_data_status(
    *,
    annual: list[AnnualAdjustedEps],
    ttm_adjusted_eps: float | None,
    close: float,
    financial_safety_status: FinancialSafetyStatus,
) -> CoreDataStatus:
    has_three_consecutive_years = (
        len(annual) == 3
        and [item.year for item in annual]
        == list(range(annual[0].year, annual[0].year + 3))
    )
    if (
        not has_three_consecutive_years
        or ttm_adjusted_eps is None
        or close <= 0
        or financial_safety_status == "insufficient_data"
    ):
        return "source_missing"
    if (
        any(item.value <= 0 for item in annual)
        or ttm_adjusted_eps <= 0
        or financial_safety_status == "invalid_equity"
    ):
        return "economic_not_applicable"
    return "complete"


def _ranking_exclusion_reason(
    financial: LynchFinancialBase,
    *,
    calculable: bool,
) -> LynchRankingExclusionReason | None:
    if not calculable:
        return "not_calculable"
    return None
