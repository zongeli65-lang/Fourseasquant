from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from fourseasquant.technical_scoring import (
    ALGORITHM_VERSION,
    DEFAULT_PARAMETERS,
    TechnicalParameters,
    TechnicalScoreView,
)
from fourseasquant.sqlite_connection import open_database_connection


TrendState = Literal["rising", "falling", "sideways"]
ElectionStatus = Literal[
    "insufficient_samples",
    "no_qualified_candidate",
    "pending_confirmation",
    "confirmed",
    "hard_invalidated",
]


class SectorMembership(BaseModel):
    sector_id: str = Field(min_length=1)
    sector_name: str = Field(min_length=1)
    code: str = Field(pattern=r"^\d{6}$")
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True
    change_reason: str
    evidence_refs: list[str] = Field(default_factory=list)

    def active_on(self, trading_date: date) -> bool:
        return (
            self.is_active
            and self.effective_from <= trading_date
            and (
                self.effective_to is None
                or trading_date <= self.effective_to
            )
        )


class SectorMembershipSnapshot(BaseModel):
    membership_version: str = Field(min_length=1)
    fundamental_version: str = Field(min_length=1)
    effective_date: date
    published_at: datetime
    complete: bool
    memberships: list[SectorMembership]

    @model_validator(mode="after")
    def require_complete_unique_snapshot(self) -> SectorMembershipSnapshot:
        if not self.complete:
            raise ValueError("拒绝接收不完整的板块成员快照")
        identities = [
            (item.sector_id, item.code, item.effective_from)
            for item in self.memberships
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("板块成员快照存在重复关系")
        return self


class MarketTrendSnapshot(BaseModel):
    actual_data_date: date
    trend_id: str = Field(min_length=1)
    trend_state: TrendState
    trend_changed: bool
    trend_start_date: date
    strategy_version: str = Field(min_length=1)


class TechnicalCandidate(BaseModel):
    rank: int = Field(ge=1)
    code: str
    name: str
    total_score: float
    structure_score: float
    structure_state: str
    structure_valid: bool
    active_breakout: bool
    qualification: Literal["qualified", "observation"]
    reasons: list[str]


class SectorLeaderResult(BaseModel):
    actual_data_date: date
    sector_id: str
    sector_name: str
    membership_version: str
    membership_effective_date: date
    membership_stale: bool
    trend_id: str
    trend_state: TrendState
    status: ElectionStatus
    leader_code: str | None
    leader_name: str | None
    technical_strength: float | None
    leader_context: Literal["with_trend", "counter_trend", "sideways"] | None
    pending_code: str | None
    pending_days: int
    valid_member_count: int
    candidates: list[TechnicalCandidate]
    explanation: str


class ElectionDay(BaseModel):
    actual_data_date: date
    membership: SectorMembershipSnapshot
    trend: MarketTrendSnapshot
    scores: list[TechnicalScoreView]

    @model_validator(mode="after")
    def dates_must_match(self) -> ElectionDay:
        if self.trend.actual_data_date != self.actual_data_date:
            raise ValueError("市场趋势日期与选举日期不一致")
        return self


class ElectionReplayResult(BaseModel):
    algorithm_version: str
    days: list[date]
    results: list[SectorLeaderResult]


class _SectorState(BaseModel):
    leader_code: str | None = None
    leader_name: str | None = None
    trend_id: str | None = None
    pending_code: str | None = None
    pending_days: int = 0
    member_streaks: dict[str, int] = Field(default_factory=dict)


def elect_sector_leaders(
    days: list[ElectionDay],
    *,
    parameters: TechnicalParameters = DEFAULT_PARAMETERS,
    algorithm_version: str = ALGORITHM_VERSION,
) -> ElectionReplayResult:
    """Replay deterministic sector elections through one deep interface."""
    ordered = sorted(days, key=lambda item: item.actual_data_date)
    states: dict[str, _SectorState] = {}
    output: list[SectorLeaderResult] = []
    for day in ordered:
        scores = {score.code: score for score in day.scores}
        sectors = _members_by_sector(day.membership, day.actual_data_date)
        for sector_id in sorted(sectors):
            members = sectors[sector_id]
            state = states.setdefault(sector_id, _SectorState())
            output.append(
                _elect_one_sector(
                    day,
                    members,
                    scores,
                    state,
                    parameters,
                )
            )
    return ElectionReplayResult(
        algorithm_version=algorithm_version,
        days=[item.actual_data_date for item in ordered],
        results=output,
    )


def save_membership_snapshot(
    path: Path, snapshot: SectorMembershipSnapshot
) -> None:
    payload = snapshot.model_dump_json()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with open_database_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO sector_membership_snapshots (
                membership_version, effective_date, payload_json,
                content_sha256, published_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(membership_version, effective_date) DO UPDATE SET
                payload_json = excluded.payload_json,
                content_sha256 = excluded.content_sha256,
                published_at = excluded.published_at
            """,
            (
                snapshot.membership_version,
                snapshot.effective_date.isoformat(),
                payload,
                digest,
                snapshot.published_at.isoformat(),
            ),
        )


def read_latest_membership_snapshot(
    path: Path, requested_date: date
) -> SectorMembershipSnapshot | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM sector_membership_snapshots
            WHERE effective_date <= ?
            ORDER BY effective_date DESC, published_at DESC
            LIMIT 1
            """,
            (requested_date.isoformat(),),
        ).fetchone()
    return (
        SectorMembershipSnapshot.model_validate_json(cast(str, row[0]))
        if row
        else None
    )


def publish_election_results(
    path: Path,
    result: ElectionReplayResult,
    *,
    published_at: datetime | None = None,
) -> int:
    publication_time = published_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    with open_database_connection(path) as connection:
        with connection:
            for item in result.results:
                connection.execute(
                    """
                    INSERT INTO sector_leader_election_results (
                        algorithm_version, membership_version,
                        actual_data_date, sector_id, trend_id,
                        result_json, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        algorithm_version, membership_version,
                        actual_data_date, sector_id
                    ) DO UPDATE SET
                        trend_id = excluded.trend_id,
                        result_json = excluded.result_json,
                        published_at = excluded.published_at
                    """,
                    (
                        result.algorithm_version,
                        item.membership_version,
                        item.actual_data_date.isoformat(),
                        item.sector_id,
                        item.trend_id,
                        item.model_dump_json(),
                        publication_time.isoformat(),
                    ),
                )
    return len(result.results)


def membership_json_schema() -> dict[str, object]:
    return cast(
        dict[str, object], SectorMembershipSnapshot.model_json_schema()
    )


def election_json_schema() -> dict[str, object]:
    return cast(dict[str, object], ElectionReplayResult.model_json_schema())


def _members_by_sector(
    snapshot: SectorMembershipSnapshot, trading_date: date
) -> dict[str, list[SectorMembership]]:
    result: dict[str, list[SectorMembership]] = {}
    for membership in snapshot.memberships:
        if membership.active_on(trading_date):
            result.setdefault(membership.sector_id, []).append(membership)
    return result


def _elect_one_sector(
    day: ElectionDay,
    members: list[SectorMembership],
    scores: dict[str, TechnicalScoreView],
    state: _SectorState,
    parameters: TechnicalParameters,
) -> SectorLeaderResult:
    sector_id = members[0].sector_id
    sector_name = members[0].sector_name
    active_codes = {item.code for item in members}
    state.member_streaks = {
        code: state.member_streaks.get(code, 0) + 1
        for code in active_codes
    }
    available = [
        scores[code] for code in active_codes if code in scores
    ]
    ranked = sorted(
        available,
        key=lambda item: (
            -item.total_score,
            -int(item.structure_valid),
            -item.structure_score,
            -item.breakout_score,
            -item.relative_strength_score,
            item.code,
        ),
    )
    candidates = [
        _candidate(index + 1, score, parameters)
        for index, score in enumerate(ranked[:5])
    ]
    qualified = [
        score
        for score in ranked
        if score.structure_valid
        and score.structure_state != "broken"
        and score.total_score >= parameters.minimum_leader_score
    ]

    trend_reset = (
        day.trend.trend_changed
        or state.trend_id is not None
        and state.trend_id != day.trend.trend_id
    )
    state.trend_id = day.trend.trend_id
    previous_leader = (
        scores.get(state.leader_code) if state.leader_code else None
    )
    hard_invalid = (
        state.leader_code is not None
        and (
            state.leader_code not in active_codes
            or previous_leader is None
            or previous_leader.structure_state == "broken"
        )
    )
    if trend_reset:
        state.leader_code = None
        state.leader_name = None
        state.pending_code = None
        state.pending_days = 0
    elif hard_invalid:
        state.leader_code = None
        state.leader_name = None
        state.pending_code = None
        state.pending_days = 0

    if len(available) < parameters.minimum_sector_members:
        return _result(
            day,
            sector_id,
            sector_name,
            state,
            "insufficient_samples",
            available,
            candidates,
            "有效成员少于最低选举数量。",
        )
    if not qualified:
        return _result(
            day,
            sector_id,
            sector_name,
            state,
            "hard_invalidated" if hard_invalid else "no_qualified_candidate",
            available,
            candidates,
            "原龙头硬失效。" if hard_invalid else "没有结构和分数均合格的候选。",
        )

    best = qualified[0]
    if state.leader_code is None:
        _advance_pending(state, best.code)
        if (
            state.pending_days
            >= parameters.challenge_confirmation_days
            and state.member_streaks.get(best.code, 0)
            >= parameters.challenge_confirmation_days
            and not trend_reset
        ):
            _confirm(state, best)
            return _result(
                day,
                sector_id,
                sector_name,
                state,
                "confirmed",
                available,
                candidates,
                "候选连续确认后成为技术龙头。",
            )
        return _result(
            day,
            sector_id,
            sector_name,
            state,
            "pending_confirmation",
            available,
            candidates,
            "候选正在完成两日确认。",
        )

    leader = scores[state.leader_code]
    if best.code == leader.code:
        state.pending_code = None
        state.pending_days = 0
        return _result(
            day,
            sector_id,
            sector_name,
            state,
            "confirmed",
            available,
            candidates,
            "原技术龙头继续保位。",
        )
    margin = best.total_score - leader.total_score
    if (
        margin >= parameters.direct_challenge_margin
        and state.member_streaks.get(best.code, 0)
        >= parameters.challenge_confirmation_days
    ):
        _confirm(state, best)
        return _result(
            day,
            sector_id,
            sector_name,
            state,
            "confirmed",
            available,
            candidates,
            "挑战者单日显著领先，直接完成替换。",
        )
    if margin >= parameters.confirmed_challenge_margin:
        _advance_pending(state, best.code)
        if state.pending_days >= parameters.challenge_confirmation_days:
            _confirm(state, best)
            return _result(
                day,
                sector_id,
                sector_name,
                state,
                "confirmed",
                available,
                candidates,
                "挑战者连续两日领先，完成替换。",
            )
    else:
        state.pending_code = None
        state.pending_days = 0
    return _result(
        day,
        sector_id,
        sector_name,
        state,
        "confirmed",
        available,
        candidates,
        "挑战者尚未满足替换条件，原技术龙头保位。",
    )


def _candidate(
    rank: int,
    score: TechnicalScoreView,
    parameters: TechnicalParameters,
) -> TechnicalCandidate:
    qualified = (
        score.structure_valid
        and score.structure_state != "broken"
        and score.total_score >= parameters.minimum_leader_score
    )
    reasons: list[str] = []
    if not score.structure_valid:
        reasons.append("上升结构尚未完整")
    if score.structure_state == "broken":
        reasons.append("上升结构已破坏")
    if score.total_score < parameters.minimum_leader_score:
        reasons.append("技术总分低于门槛")
    if qualified:
        reasons.append("结构与分数均合格")
    return TechnicalCandidate(
        rank=rank,
        code=score.code,
        name=score.name,
        total_score=score.total_score,
        structure_score=score.structure_score,
        structure_state=score.structure_state,
        structure_valid=score.structure_valid,
        active_breakout=score.active_breakout,
        qualification="qualified" if qualified else "observation",
        reasons=reasons,
    )


def _advance_pending(state: _SectorState, code: str) -> None:
    if state.pending_code == code:
        state.pending_days += 1
    else:
        state.pending_code = code
        state.pending_days = 1


def _confirm(state: _SectorState, score: TechnicalScoreView) -> None:
    state.leader_code = score.code
    state.leader_name = score.name
    state.pending_code = None
    state.pending_days = 0


def _result(
    day: ElectionDay,
    sector_id: str,
    sector_name: str,
    state: _SectorState,
    status: ElectionStatus,
    available: list[TechnicalScoreView],
    candidates: list[TechnicalCandidate],
    explanation: str,
) -> SectorLeaderResult:
    leader = next(
        (
            score
            for score in available
            if score.code == state.leader_code
        ),
        None,
    )
    context: Literal["with_trend", "counter_trend", "sideways"] | None = None
    if leader is not None:
        context = (
            "with_trend"
            if day.trend.trend_state == "rising"
            else "counter_trend"
            if day.trend.trend_state == "falling"
            else "sideways"
        )
    return SectorLeaderResult(
        actual_data_date=day.actual_data_date,
        sector_id=sector_id,
        sector_name=sector_name,
        membership_version=day.membership.membership_version,
        membership_effective_date=day.membership.effective_date,
        membership_stale=day.membership.effective_date < day.actual_data_date,
        trend_id=day.trend.trend_id,
        trend_state=day.trend.trend_state,
        status=status,
        leader_code=leader.code if leader else None,
        leader_name=leader.name if leader else None,
        technical_strength=leader.total_score if leader else None,
        leader_context=context,
        pending_code=state.pending_code,
        pending_days=state.pending_days,
        valid_member_count=len(available),
        candidates=candidates,
        explanation=explanation,
    )
