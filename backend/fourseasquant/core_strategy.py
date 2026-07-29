from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


CORE_STRATEGY_VERSION = "core-strategy-v1"
FUNDAMENTAL_FRESHNESS_DAYS = 35

CandidateRoute = Literal[
    "industry_chain",
    "technical_mainline",
    "pure_technical",
]
OpportunityGrade = Literal["S", "A", "B", "C"]
FundamentalInvestigationStatus = Literal[
    "queued",
    "running",
    "complete",
    "incomplete",
    "failed",
]
FundamentalConclusion = Literal[
    "hard",
    "large_cap_shortcut",
    "ordinary",
    "hot_money_risk",
    "unknown",
]
FundamentalState = Literal[
    "not_required",
    "needs_investigation",
    "in_progress",
    "ready",
    "incomplete",
    "failed",
    "stale",
]
FundamentalPriority = Literal["preferred", "neutral", "deprioritized"]
InvestigationReason = Literal["missing", "incomplete", "failed", "stale"]


# 调查模块可以分批计算这些指标；
# 策略只消费覆盖状态和最终证据，
# 不在初筛阶段读取任何一个指标。
FUNDAMENTAL_REQUIRED_METRICS: tuple[str, ...] = (
    "adjusted_profit_positive",
    "adjusted_pe",
    "adjusted_pe_peer_percentile",
    "adjusted_pe_peer_sample_size",
    "adjusted_pe_historical_percentile",
    "adjusted_pe_historical_sample_size",
    "five_year_adjusted_eps_cagr",
    "positive_growth_intervals",
    "free_cash_flow_per_share",
    "net_cash_per_share",
    "debt_to_equity",
    "lynch_ratio",
    "main_business_profit_share",
    "true_money_signal_score",
    "floating_market_cap",
)


class FundamentalInvestigation(BaseModel):
    """基本面调查模块回填给核心策略的最小结果契约。"""

    status: FundamentalInvestigationStatus
    rules_version: str = Field(min_length=1)
    as_of_date: date | None = None
    updated_at: datetime
    conclusion: FundamentalConclusion | None = None
    available_metrics: list[str] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    error_summary: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.updated_at.tzinfo is None:
            raise ValueError("基本面调查更新时间必须包含时区")
        if len(set(self.available_metrics)) != len(self.available_metrics):
            raise ValueError("已计算基本面指标不能重复")
        if len(set(self.missing_metrics)) != len(self.missing_metrics):
            raise ValueError("缺失基本面指标不能重复")
        if set(self.available_metrics) & set(self.missing_metrics):
            raise ValueError("同一指标不能同时标记为已计算和缺失")

        if self.status == "complete":
            if self.as_of_date is None:
                raise ValueError("完整基本面调查必须包含数据日期")
            if self.conclusion is None:
                raise ValueError("完整基本面调查必须包含证据结论")
            if self.missing_metrics:
                raise ValueError("完整基本面调查不能仍有缺失指标")
        elif self.status == "incomplete":
            if not self.missing_metrics:
                raise ValueError("不完整基本面调查必须列出缺失指标")
            if self.conclusion not in {None, "unknown"}:
                raise ValueError(
                    "不完整基本面调查不能提前形成正式证据结论"
                )
        elif self.status == "failed":
            if not self.error_summary or not self.error_summary.strip():
                raise ValueError("失败的基本面调查必须保留失败原因")
            if self.conclusion is not None:
                raise ValueError("失败的基本面调查不能形成证据结论")
        elif self.conclusion is not None:
            raise ValueError(
                "排队或调查中的基本面任务不能提前形成证据结论"
            )

        return self


class PreliminaryCandidate(BaseModel):
    """市场、产业链和技术排行已经产生的初筛候选。"""

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    route: CandidateRoute
    base_grade: OpportunityGrade
    preliminary_rank: int = Field(ge=1)
    technical_trade_permission: bool
    fundamental: FundamentalInvestigation | None = None


class CandidatePipelineInput(BaseModel):
    """候选证据流水线的唯一输入。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    candidates: list[PreliminaryCandidate]

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        codes = [candidate.code for candidate in self.candidates]
        if len(set(codes)) != len(codes):
            raise ValueError("候选股票代码不能重复")
        for candidate in self.candidates:
            investigation = candidate.fundamental
            if (
                investigation is not None
                and investigation.as_of_date is not None
                and investigation.as_of_date > self.actual_date
            ):
                raise ValueError("基本面调查不得使用目标日之后的数据")
        return self


class CandidateEvidenceDecision(BaseModel):
    """策略对一只初筛候选的证据状态解释。"""

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    route: CandidateRoute
    base_grade: OpportunityGrade
    preliminary_rank: int
    technical_trade_permission: bool
    retained: Literal[True] = True
    fundamental_state: FundamentalState
    fundamental_conclusion: FundamentalConclusion | None = None
    fundamental_priority: FundamentalPriority = "neutral"
    fundamental_as_of_date: date | None = None
    available_metrics: list[str] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)
    evidence_reasons: list[str] = Field(default_factory=list)
    error_summary: str | None = None


class FundamentalInvestigationTarget(BaseModel):
    """核心策略发布给基本面调查模块的有序目标。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    preliminary_rank: int = Field(ge=1)
    reason: InvestigationReason
    required_metrics: list[str] = Field(min_length=1)
    available_metrics: list[str] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)


class CandidatePipelineDecision(BaseModel):
    """一次纯计算同时返回候选证据状态和后续调查目标。"""

    actual_date: date
    strategy_version: str
    candidates: list[CandidateEvidenceDecision]
    fundamental_targets: list[FundamentalInvestigationTarget]


def evaluate_candidate_pipeline(
    source: CandidatePipelineInput,
) -> CandidatePipelineDecision:
    """
    解释初筛后的基本面覆盖状态。

    不变量：
    - 不删除候选；
    - 不改变基础等级；
    - 不改变技术交易许可；
    - 只为缺失、不完整、失败或过期证据生成调查目标。
    """

    decisions: list[CandidateEvidenceDecision] = []
    targets: list[FundamentalInvestigationTarget] = []

    for candidate in source.candidates:
        decision, target = _evaluate_candidate(
            candidate,
            actual_date=source.actual_date,
            strategy_version=source.strategy_version,
        )
        decisions.append(decision)
        if target is not None:
            targets.append(target)

    targets.sort(key=lambda item: (item.preliminary_rank, item.code))
    return CandidatePipelineDecision(
        actual_date=source.actual_date,
        strategy_version=source.strategy_version,
        candidates=decisions,
        fundamental_targets=targets,
    )


def _evaluate_candidate(
    candidate: PreliminaryCandidate,
    *,
    actual_date: date,
    strategy_version: str,
) -> tuple[CandidateEvidenceDecision, FundamentalInvestigationTarget | None]:
    investigation = candidate.fundamental
    if candidate.route == "pure_technical":
        return _candidate_decision(
            candidate,
            state="not_required",
            investigation=investigation,
        ), None

    if investigation is None:
        return _candidate_decision(
            candidate,
            state="needs_investigation",
            investigation=None,
        ), _target(
            candidate,
            actual_date=actual_date,
            strategy_version=strategy_version,
            reason="missing",
            investigation=None,
        )

    if investigation.status in {"queued", "running"}:
        return _candidate_decision(
            candidate,
            state="in_progress",
            investigation=investigation,
        ), None

    if investigation.status == "incomplete":
        return _candidate_decision(
            candidate,
            state="incomplete",
            investigation=investigation,
        ), _target(
            candidate,
            actual_date=actual_date,
            strategy_version=strategy_version,
            reason="incomplete",
            investigation=investigation,
        )

    if investigation.status == "failed":
        return _candidate_decision(
            candidate,
            state="failed",
            investigation=investigation,
        ), _target(
            candidate,
            actual_date=actual_date,
            strategy_version=strategy_version,
            reason="failed",
            investigation=investigation,
        )

    assert investigation.status == "complete"
    assert investigation.as_of_date is not None
    age_days = (actual_date - investigation.as_of_date).days
    if age_days > FUNDAMENTAL_FRESHNESS_DAYS:
        return _candidate_decision(
            candidate,
            state="stale",
            investigation=investigation,
        ), _target(
            candidate,
            actual_date=actual_date,
            strategy_version=strategy_version,
            reason="stale",
            investigation=investigation,
        )

    return _candidate_decision(
        candidate,
        state="ready",
        investigation=investigation,
        priority=_fundamental_priority(investigation.conclusion),
    ), None


def _candidate_decision(
    candidate: PreliminaryCandidate,
    *,
    state: FundamentalState,
    investigation: FundamentalInvestigation | None,
    priority: FundamentalPriority = "neutral",
) -> CandidateEvidenceDecision:
    return CandidateEvidenceDecision(
        code=candidate.code,
        name=candidate.name,
        route=candidate.route,
        base_grade=candidate.base_grade,
        preliminary_rank=candidate.preliminary_rank,
        technical_trade_permission=candidate.technical_trade_permission,
        fundamental_state=state,
        fundamental_conclusion=(
            investigation.conclusion if investigation is not None else None
        ),
        fundamental_priority=priority,
        fundamental_as_of_date=(
            investigation.as_of_date if investigation is not None else None
        ),
        available_metrics=(
            list(investigation.available_metrics)
            if investigation is not None
            else []
        ),
        missing_metrics=(
            list(investigation.missing_metrics)
            if investigation is not None
            else []
        ),
        evidence_reasons=(
            list(investigation.reasons) if investigation is not None else []
        ),
        error_summary=(
            investigation.error_summary if investigation is not None else None
        ),
    )


def _target(
    candidate: PreliminaryCandidate,
    *,
    actual_date: date,
    strategy_version: str,
    reason: InvestigationReason,
    investigation: FundamentalInvestigation | None,
) -> FundamentalInvestigationTarget:
    return FundamentalInvestigationTarget(
        actual_date=actual_date,
        strategy_version=strategy_version,
        code=candidate.code,
        name=candidate.name,
        preliminary_rank=candidate.preliminary_rank,
        reason=reason,
        required_metrics=list(FUNDAMENTAL_REQUIRED_METRICS),
        available_metrics=(
            list(investigation.available_metrics)
            if investigation is not None
            else []
        ),
        missing_metrics=(
            list(investigation.missing_metrics)
            if investigation is not None
            else list(FUNDAMENTAL_REQUIRED_METRICS)
        ),
    )


def _fundamental_priority(
    conclusion: FundamentalConclusion | None,
) -> FundamentalPriority:
    if conclusion in {"hard", "large_cap_shortcut"}:
        return "preferred"
    if conclusion == "hot_money_risk":
        return "deprioritized"
    return "neutral"
