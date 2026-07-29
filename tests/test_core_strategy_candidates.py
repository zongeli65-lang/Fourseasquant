from datetime import date

from fourseasquant.core_strategy_candidates import (
    CandidateUniverseInput,
    IndustryChainCandidateEvidence,
    RankingMetric,
    TechnicalRankingEvidence,
    build_candidate_universe,
)
from fourseasquant.technical_scoring import Board


ACTUAL_DATE = date(2026, 7, 28)


def _rank(
    code: str,
    *,
    metric: RankingMetric,
    rank: int,
    board: Board = "main",
    name: str = "测试公司",
) -> TechnicalRankingEvidence:
    return TechnicalRankingEvidence(
        code=code,
        name=name,
        board=board,
        metric=metric,
        market_rank=rank,
        score=88,
        turnover_score=50,
    )


def test_sideways_uses_only_pure_technical_and_best_rank_grade() -> None:
    result = build_candidate_universe(
        CandidateUniverseInput(
            actual_date=ACTUAL_DATE,
            market_state="sideways",
            technical_snapshot_complete=True,
            industry_snapshot_complete=True,
            technical_rankings=[
                _rank("600001", metric="structure_score", rank=17),
                _rank(
                    "600001",
                    metric="relative_strength_score",
                    rank=8,
                ),
                _rank("600002", metric="breakout_score", rank=20),
            ],
            industry_candidates=[
                IndustryChainCandidateEvidence(
                    code="600003",
                    name="产业链公司",
                    selection_ref="selection:1",
                )
            ],
        )
    )

    assert [item.code for item in result.candidates] == [
        "600001",
        "600002",
    ]
    assert result.candidates[0].route == "pure_technical"
    assert result.candidates[0].base_grade == "B"
    assert result.candidates[1].base_grade == "C"


def test_rising_industry_overlap_keeps_one_b_candidate_without_upgrade() -> None:
    result = build_candidate_universe(
        CandidateUniverseInput(
            actual_date=ACTUAL_DATE,
            market_state="rising",
            technical_snapshot_complete=True,
            industry_snapshot_complete=True,
            technical_rankings=[
                _rank("600001", metric="structure_score", rank=19),
            ],
            industry_candidates=[
                IndustryChainCandidateEvidence(
                    code="600001",
                    name="产业链公司",
                    selection_ref="selection:1",
                ),
                IndustryChainCandidateEvidence(
                    code="600002",
                    name="产业链二号",
                    selection_ref="selection:2",
                ),
            ],
        )
    )

    assert [item.code for item in result.candidates] == [
        "600001",
        "600002",
    ]
    overlap = result.candidates[0]
    assert overlap.route == "industry_chain"
    assert overlap.base_grade == "B"
    assert overlap.preliminary_rank == 19
    assert len(overlap.technical_rankings) == 1


def test_board_and_name_filters_do_not_recalculate_market_rank() -> None:
    result = build_candidate_universe(
        CandidateUniverseInput(
            actual_date=ACTUAL_DATE,
            market_state="falling",
            technical_snapshot_complete=True,
            industry_snapshot_complete=False,
            technical_rankings=[
                _rank(
                    "300001",
                    metric="structure_score",
                    rank=1,
                    board="chinext",
                ),
                _rank(
                    "600001",
                    metric="structure_score",
                    rank=11,
                ),
                _rank(
                    "600002",
                    metric="structure_score",
                    rank=12,
                    name="*ST示例",
                ),
            ],
            industry_candidates=[],
        )
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].code == "600001"
    assert result.candidates[0].preliminary_rank == 11
    assert result.candidates[0].base_grade == "C"


def test_missing_technical_snapshot_never_reuses_partial_rankings() -> None:
    result = build_candidate_universe(
        CandidateUniverseInput(
            actual_date=ACTUAL_DATE,
            market_state="falling",
            technical_snapshot_complete=False,
            industry_snapshot_complete=False,
            technical_rankings=[
                _rank("600001", metric="structure_score", rank=1),
            ],
            industry_candidates=[],
        )
    )

    assert result.candidates == []
    assert result.reasons == ["technical_snapshot_unavailable"]
