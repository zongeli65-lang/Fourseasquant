from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fourseasquant.sector_leadership import (
    ElectionDay,
    MarketTrendSnapshot,
    SectorMembership,
    SectorMembershipSnapshot,
    elect_sector_leaders,
)
from fourseasquant.technical_scoring import TechnicalScoreView


def _score(
    trading_date: date,
    code: str,
    total: float,
    *,
    valid: bool = True,
    state: str = "candidate",
) -> TechnicalScoreView:
    return TechnicalScoreView.model_validate(
        {
            "version": "technical-v1",
            "actual_data_date": trading_date,
            "code": code,
            "name": f"样本{code}",
            "board": "main",
            "ema3": 10,
            "derivative": 0.1,
            "derivative_state": "positive",
            "zero_threshold": 0.02,
            "atr10": 0.2,
            "structure_state": state,
            "structure_valid": valid,
            "active_breakout": True,
            "structure_score": min(total, 55),
            "breakout_score": max(0, min(total - 55, 20)),
            "relative_strength_score": 10,
            "turnover_score": 5,
            "total_score": total,
            "maxima": [],
            "minima": [],
            "evidence": {},
        }
    )


def _membership(effective_date: date) -> SectorMembershipSnapshot:
    return SectorMembershipSnapshot(
        membership_version="fundamental-members-v1",
        fundamental_version="fundamental-v1",
        effective_date=effective_date,
        published_at=datetime(
            2026, 7, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        complete=True,
        memberships=[
            SectorMembership(
                sector_id="power",
                sector_name="电力",
                code=code,
                effective_from=effective_date,
                change_reason="初始化",
            )
            for code in ("600001", "600002", "600003")
        ],
    )


def _day(
    trading_date: date,
    scores: list[TechnicalScoreView],
    *,
    changed: bool = False,
) -> ElectionDay:
    return ElectionDay(
        actual_data_date=trading_date,
        membership=_membership(date(2026, 7, 20)),
        trend=MarketTrendSnapshot(
            actual_data_date=trading_date,
            trend_id="trend-1",
            trend_state="rising",
            trend_changed=changed,
            trend_start_date=date(2026, 7, 20),
            strategy_version="strategy-v1",
        ),
        scores=scores,
    )


def test_election_waits_two_days_then_confirms_and_replaces_by_margin() -> None:
    first = date(2026, 7, 20)
    second = date(2026, 7, 21)
    third = date(2026, 7, 22)
    replay = elect_sector_leaders(
        [
            _day(
                first,
                [
                    _score(first, "600001", 82),
                    _score(first, "600002", 75),
                    _score(first, "600003", 70),
                ],
                changed=True,
            ),
            _day(
                second,
                [
                    _score(second, "600001", 83),
                    _score(second, "600002", 76),
                    _score(second, "600003", 71),
                ],
            ),
            _day(
                third,
                [
                    _score(third, "600001", 78),
                    _score(third, "600002", 90),
                    _score(third, "600003", 72),
                ],
            ),
        ]
    )

    assert replay.results[0].status == "pending_confirmation"
    assert replay.results[0].leader_code is None
    assert replay.results[1].status == "confirmed"
    assert replay.results[1].leader_code == "600001"
    assert replay.results[1].technical_strength == 83
    assert replay.results[1].membership_stale is True
    assert replay.results[2].status == "confirmed"
    assert replay.results[2].leader_code == "600002"
    assert replay.results[2].explanation == "挑战者单日显著领先，直接完成替换。"


def test_election_keeps_incomplete_breakout_as_observation_only() -> None:
    trading_date = date(2026, 7, 20)
    result = elect_sector_leaders(
        [
            _day(
                trading_date,
                [
                    _score(
                        trading_date,
                        "600001",
                        95,
                        valid=False,
                        state="forming",
                    ),
                    _score(trading_date, "600002", 60),
                    _score(trading_date, "600003", 55),
                ],
            )
        ]
    ).results[0]

    assert result.status == "no_qualified_candidate"
    assert result.leader_code is None
    assert result.candidates[0].code == "600001"
    assert result.candidates[0].qualification == "observation"
