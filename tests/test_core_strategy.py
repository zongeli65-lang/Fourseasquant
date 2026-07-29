from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from fourseasquant.core_strategy import (
    CORE_STRATEGY_VERSION,
    CandidatePipelineDecision,
    CandidateRoute,
    CandidatePipelineInput,
    FundamentalConclusion,
    FundamentalInvestigation,
    FundamentalInvestigationStatus,
    OpportunityGrade,
    PreliminaryCandidate,
    evaluate_candidate_pipeline,
)


BEIJING = ZoneInfo("Asia/Shanghai")
TARGET_DATE = date(2026, 7, 29)


def _candidate(
    code: str = "600000",
    *,
    route: CandidateRoute = "technical_mainline",
    grade: OpportunityGrade = "B",
    rank: int = 1,
    technical_trade_permission: bool = True,
    fundamental: FundamentalInvestigation | None = None,
) -> PreliminaryCandidate:
    return PreliminaryCandidate(
        code=code,
        name=f"股票{code}",
        route=route,
        base_grade=grade,
        preliminary_rank=rank,
        technical_trade_permission=technical_trade_permission,
        fundamental=fundamental,
    )


def _investigation(
    *,
    status: FundamentalInvestigationStatus,
    as_of_date: date | None = TARGET_DATE,
    conclusion: FundamentalConclusion | None = None,
    available_metrics: list[str] | None = None,
    missing_metrics: list[str] | None = None,
    error_summary: str | None = None,
) -> FundamentalInvestigation:
    return FundamentalInvestigation(
        status=status,
        rules_version="personal-fundamental-v1",
        as_of_date=as_of_date,
        updated_at=datetime(2026, 7, 29, 18, tzinfo=BEIJING),
        conclusion=conclusion,
        available_metrics=available_metrics or [],
        missing_metrics=missing_metrics or [],
        reasons=[],
        error_summary=error_summary,
    )


def _evaluate(
    *candidates: PreliminaryCandidate,
) -> CandidatePipelineDecision:
    return evaluate_candidate_pipeline(
        CandidatePipelineInput(
            actual_date=TARGET_DATE,
            strategy_version=CORE_STRATEGY_VERSION,
            candidates=list(candidates),
        )
    )


def test_missing_fundamental_creates_post_screen_request_without_filtering() -> None:
    candidate = _candidate()

    decision = _evaluate(candidate)

    assert [item.code for item in decision.candidates] == ["600000"]
    item = decision.candidates[0]
    assert item.retained is True
    assert item.base_grade == "B"
    assert item.technical_trade_permission is True
    assert item.fundamental_state == "needs_investigation"
    assert item.fundamental_priority == "neutral"
    assert [
        (target.code, target.reason)
        for target in decision.fundamental_targets
    ] == [("600000", "missing")]


def test_pure_technical_candidate_does_not_depend_on_fundamental() -> None:
    candidate = _candidate(route="pure_technical")

    decision = _evaluate(candidate)

    assert decision.candidates[0].fundamental_state == "not_required"
    assert decision.candidates[0].retained is True
    assert decision.fundamental_targets == []


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_investigation_is_not_duplicated(
    status: Literal["queued", "running"],
) -> None:
    candidate = _candidate(
        fundamental=_investigation(
            status=status,
            as_of_date=None,
        )
    )

    decision = _evaluate(candidate)

    assert decision.candidates[0].fundamental_state == "in_progress"
    assert decision.fundamental_targets == []


def test_complete_hard_result_only_adds_selection_priority() -> None:
    candidate = _candidate(
        grade="C",
        technical_trade_permission=False,
        fundamental=_investigation(
            status="complete",
            conclusion="hard",
            available_metrics=["adjusted_pe", "main_business_profit_share"],
        ),
    )

    decision = _evaluate(candidate)

    item = decision.candidates[0]
    assert item.fundamental_state == "ready"
    assert item.fundamental_conclusion == "hard"
    assert item.fundamental_priority == "preferred"
    assert item.base_grade == "C"
    assert item.technical_trade_permission is False
    assert item.retained is True
    assert decision.fundamental_targets == []


def test_hot_money_risk_cannot_veto_or_downgrade_trade_grade() -> None:
    candidate = _candidate(
        grade="B",
        technical_trade_permission=True,
        fundamental=_investigation(
            status="complete",
            conclusion="hot_money_risk",
            available_metrics=["adjusted_pe"],
        ),
    )

    decision = _evaluate(candidate)

    item = decision.candidates[0]
    assert item.fundamental_priority == "deprioritized"
    assert item.base_grade == "B"
    assert item.technical_trade_permission is True
    assert item.retained is True


@pytest.mark.parametrize(
    ("status", "expected_state", "expected_reason"),
    [
        ("incomplete", "incomplete", "incomplete"),
        ("failed", "failed", "failed"),
    ],
)
def test_noncomplete_result_is_retained_and_requested_again(
    status: str,
    expected_state: str,
    expected_reason: str,
) -> None:
    investigation = (
        _investigation(
            status="incomplete",
            conclusion=None,
            available_metrics=["adjusted_pe"],
            missing_metrics=["main_business_profit_share"],
        )
        if status == "incomplete"
        else _investigation(
            status="failed",
            as_of_date=None,
            error_summary="上游暂时不可用",
        )
    )
    candidate = _candidate(fundamental=investigation)

    decision = _evaluate(candidate)

    item = decision.candidates[0]
    assert item.fundamental_state == expected_state
    assert item.fundamental_priority == "neutral"
    assert item.retained is True
    assert decision.fundamental_targets[0].reason == expected_reason


def test_complete_result_older_than_35_natural_days_requests_refresh() -> None:
    candidate = _candidate(
        fundamental=_investigation(
            status="complete",
            as_of_date=TARGET_DATE - timedelta(days=36),
            conclusion="hard",
            available_metrics=["adjusted_pe"],
        )
    )

    decision = _evaluate(candidate)

    item = decision.candidates[0]
    assert item.fundamental_state == "stale"
    assert item.fundamental_priority == "neutral"
    assert item.fundamental_conclusion == "hard"
    assert decision.fundamental_targets[0].reason == "stale"


def test_targets_follow_preliminary_rank_without_a_hidden_daily_cap() -> None:
    candidates = [
        _candidate(f"{600000 + index:06d}", rank=25 - index)
        for index in range(25)
    ]

    decision = _evaluate(*candidates)

    assert len(decision.fundamental_targets) == 25
    assert [item.preliminary_rank for item in decision.fundamental_targets] == list(
        range(1, 26)
    )


def test_duplicate_candidate_codes_are_rejected_at_interface() -> None:
    with pytest.raises(ValidationError, match="候选股票代码不能重复"):
        CandidatePipelineInput(
            actual_date=TARGET_DATE,
            strategy_version=CORE_STRATEGY_VERSION,
            candidates=[_candidate(), _candidate()],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "complete",
            "as_of_date": TARGET_DATE,
            "conclusion": None,
        },
        {
            "status": "complete",
            "as_of_date": TARGET_DATE,
            "conclusion": "ordinary",
            "missing_metrics": ["free_cash_flow_per_share"],
        },
        {
            "status": "incomplete",
            "as_of_date": TARGET_DATE,
            "conclusion": None,
        },
        {
            "status": "failed",
            "as_of_date": None,
            "conclusion": None,
        },
    ],
)
def test_invalid_investigation_states_are_rejected(
    payload: dict[str, object],
) -> None:
    complete_payload: dict[str, object] = {
        "status": "complete",
        "rules_version": "personal-fundamental-v1",
        "as_of_date": TARGET_DATE,
        "updated_at": datetime(2026, 7, 29, 18, tzinfo=BEIJING),
        "conclusion": "ordinary",
        "available_metrics": [],
        "missing_metrics": [],
        "reasons": [],
        "error_summary": None,
    }
    complete_payload.update(payload)
    with pytest.raises(ValidationError):
        FundamentalInvestigation.model_validate(complete_payload)
