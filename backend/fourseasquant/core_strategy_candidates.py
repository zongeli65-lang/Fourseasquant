from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from fourseasquant.core_strategy import CandidateRoute
from fourseasquant.core_strategy_execution import (
    BaseOpportunityGrade,
    MarketState,
)
from fourseasquant.technical_scoring import Board


RankingMetric = Literal[
    "structure_score",
    "breakout_score",
    "relative_strength_score",
]


class TechnicalRankingEvidence(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    board: Board
    metric: RankingMetric
    market_rank: int = Field(ge=1)
    score: float
    turnover_score: float


class IndustryChainCandidateEvidence(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    selection_ref: str = Field(min_length=1)


class CandidateUniverseInput(BaseModel):
    """初筛候选生成器的完整、无数据库输入。"""

    actual_date: date
    market_state: MarketState
    technical_snapshot_complete: bool
    industry_snapshot_complete: bool
    technical_rankings: list[TechnicalRankingEvidence]
    industry_candidates: list[IndustryChainCandidateEvidence]

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        ranking_keys = [
            (item.code, item.metric)
            for item in self.technical_rankings
        ]
        if len(ranking_keys) != len(set(ranking_keys)):
            raise ValueError("同一股票在同一技术榜只能出现一次")
        return self


class PreliminaryCandidateSeed(BaseModel):
    code: str
    name: str
    route: CandidateRoute
    base_grade: BaseOpportunityGrade
    preliminary_rank: int
    technical_rankings: list[TechnicalRankingEvidence]
    industry_selection_refs: list[str]


class CandidateUniverseDecision(BaseModel):
    actual_date: date
    market_state: MarketState
    candidates: list[PreliminaryCandidateSeed]
    reasons: list[str]


def build_candidate_universe(
    source: CandidateUniverseInput,
) -> CandidateUniverseDecision:
    """
    生成不含基本面、舆论和交易信号的初筛候选并集。

    技术名次必须是筛选板块前计算的全市场名次；
    本模块只保留主板。
    """

    reasons: list[str] = []
    merged: dict[str, PreliminaryCandidateSeed] = {}
    if source.technical_snapshot_complete:
        _add_technical_candidates(source, merged)
    else:
        reasons.append("technical_snapshot_unavailable")

    if source.market_state == "rising":
        if source.industry_snapshot_complete:
            _add_industry_candidates(source, merged)
        else:
            reasons.append("industry_snapshot_unavailable")

    candidates = sorted(
        merged.values(),
        key=lambda item: (
            0
            if (
                source.market_state == "rising"
                and item.route == "industry_chain"
            )
            else 1,
            item.preliminary_rank,
            item.code,
        ),
    )
    return CandidateUniverseDecision(
        actual_date=source.actual_date,
        market_state=source.market_state,
        candidates=candidates,
        reasons=reasons,
    )


def _add_technical_candidates(
    source: CandidateUniverseInput,
    merged: dict[str, PreliminaryCandidateSeed],
) -> None:
    grouped: dict[str, list[TechnicalRankingEvidence]] = {}
    for evidence in source.technical_rankings:
        if (
            evidence.board != "main"
            or evidence.market_rank > 20
            or _excluded_name(evidence.name)
        ):
            continue
        grouped.setdefault(evidence.code, []).append(evidence)

    route: CandidateRoute = (
        "technical_mainline"
        if source.market_state == "rising"
        else "pure_technical"
    )
    for code, evidence_list in grouped.items():
        ordered = sorted(
            evidence_list,
            key=lambda item: (item.market_rank, item.metric),
        )
        best_rank = min(item.market_rank for item in ordered)
        merged[code] = PreliminaryCandidateSeed(
            code=code,
            name=ordered[0].name,
            route=route,
            base_grade="B" if best_rank <= 10 else "C",
            preliminary_rank=best_rank,
            technical_rankings=ordered,
            industry_selection_refs=[],
        )


def _add_industry_candidates(
    source: CandidateUniverseInput,
    merged: dict[str, PreliminaryCandidateSeed],
) -> None:
    for evidence in source.industry_candidates:
        if _excluded_name(evidence.name):
            continue
        existing = merged.get(evidence.code)
        if existing is None:
            merged[evidence.code] = PreliminaryCandidateSeed(
                code=evidence.code,
                name=evidence.name,
                route="industry_chain",
                base_grade="B",
                preliminary_rank=21,
                technical_rankings=[],
                industry_selection_refs=[evidence.selection_ref],
            )
            continue
        existing.route = "industry_chain"
        existing.base_grade = "B"
        existing.industry_selection_refs = list(
            dict.fromkeys(
                (
                    *existing.industry_selection_refs,
                    evidence.selection_ref,
                )
            )
        )


def _excluded_name(name: str) -> bool:
    upper = name.upper()
    return "ST" in upper or "退" in name
