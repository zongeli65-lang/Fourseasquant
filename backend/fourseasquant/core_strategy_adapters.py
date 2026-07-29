from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from fourseasquant.akshare_history import HISTORY_SOURCE
from fourseasquant.core_strategy import PreliminaryCandidate
from fourseasquant.core_strategy_candidates import (
    CandidateUniverseInput,
    IndustryChainCandidateEvidence,
    PreliminaryCandidateSeed,
    RankingMetric,
    TechnicalRankingEvidence,
    build_candidate_universe,
)
from fourseasquant.core_strategy_repository import CoreStrategyInputVersions
from fourseasquant.core_strategy_execution import MarketState
from fourseasquant.core_strategy_positions import (
    HoldingPosition,
    PositionObservation,
)
from fourseasquant.core_strategy_technical import (
    DailyTechnicalAnalysis,
    DailyTechnicalBar,
    DailyTechnicalInput,
    analyze_daily_technical,
)
from fourseasquant.market_environment import (
    MarketEnvironmentUnavailable,
    read_market_environment,
)
from fourseasquant.public_opinion_deepseek import (
    PROMPT_VERSION as OPINION_PROMPT_VERSION,
)
from fourseasquant.public_opinion_repository import (
    PublicOpinionWindow,
    read_public_opinion_window,
)
from fourseasquant.sqlite_connection import open_database_connection
from fourseasquant.technical_scoring import Board, DerivativeState


class StrategyInputUnavailable(LookupError):
    """完整策略日输入尚不可用。"""


class RawDailyBar(BaseModel):
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    previous_close: float = Field(gt=0)
    change_pct: float
    volume: int = Field(ge=0)
    turnover_cny: int = Field(ge=0)
    listing_trading_days: int = Field(ge=0)


class PreparedStrategyCandidate(BaseModel):
    preliminary: PreliminaryCandidate
    technical: DailyTechnicalAnalysis | None
    technical_rankings: list[TechnicalRankingEvidence]
    industry_selection_refs: list[str]
    raw_bar: RawDailyBar
    input_reasons: list[str] = Field(default_factory=list)


class CoreStrategyDailyInputs(BaseModel):
    requested_date: date
    actual_date: date
    market_state: MarketState
    market_data_complete: bool
    corporate_actions_complete: bool
    technical_version: str
    qfq_source: str
    input_versions: CoreStrategyInputVersions
    candidates: list[PreparedStrategyCandidate]
    reasons: list[str]


class StrategyOpinionEvidence(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    targeted: bool
    status: Literal[
        "not_targeted",
        "collecting",
        "insufficient_sample",
        "favorable",
        "unfavorable",
        "balanced",
    ]
    platform: Literal["sina"] | None = None
    window_days: Literal[1, 3, 7] | None = None
    direction_index: float | None = None
    rules_version: str | None = None


class PreparedPositionObservations(BaseModel):
    actual_date: date
    observations: list[PositionObservation]
    unavailable_codes: list[str]


def load_core_strategy_daily_inputs(
    path: Path,
    *,
    requested_date: date,
    corporate_actions_complete: bool,
) -> CoreStrategyDailyInputs:
    """
    从已发布数据库事实构造同一实际日期的核心策略输入。

    市场、K 线和技术评分没有形成同日完整版本时直接拒绝返回；
    产业链为空可以是完整的空事实，不会据此伪造候选。
    """

    try:
        market = read_market_environment(
            path,
            requested_date=requested_date,
        )
    except (MarketEnvironmentUnavailable, sqlite3.OperationalError) as error:
        raise StrategyInputUnavailable("市场环境快照不可用") from error
    actual_date = market.actual_data_date
    technical_version, qfq_source = _technical_publication(
        path,
        actual_date=actual_date,
    )
    _require_candle_publication(
        path,
        actual_date=actual_date,
        qfq_source=qfq_source,
    )
    rankings = _read_technical_rankings(
        path,
        actual_date=actual_date,
        technical_version=technical_version,
        qfq_source=qfq_source,
    )
    industry_candidates, industry_version, industry_complete = (
        _read_industry_candidates(
            path,
            actual_date=actual_date,
        )
    )
    eligible_industry, industry_reasons = _eligible_industry_candidates(
        path,
        actual_date=actual_date,
        candidates=industry_candidates,
    )
    universe = build_candidate_universe(
        CandidateUniverseInput(
            actual_date=actual_date,
            market_state=market.trend_state,
            technical_snapshot_complete=True,
            industry_snapshot_complete=industry_complete,
            technical_rankings=rankings,
            industry_candidates=eligible_industry,
        )
    )
    prepared: list[PreparedStrategyCandidate] = []
    for seed in universe.candidates:
        candidate = _prepare_candidate(
            path,
            seed=seed,
            actual_date=actual_date,
            technical_version=technical_version,
            qfq_source=qfq_source,
            corporate_actions_complete=corporate_actions_complete,
        )
        if candidate is not None:
            prepared.append(candidate)

    return CoreStrategyDailyInputs(
        requested_date=requested_date,
        actual_date=actual_date,
        market_state=market.trend_state,
        market_data_complete=True,
        corporate_actions_complete=corporate_actions_complete,
        technical_version=technical_version,
        qfq_source=qfq_source,
        input_versions=CoreStrategyInputVersions(
            market_environment=(
                f"{market.rules_version}:{market.trend_id}"
            ),
            technical_scores=(
                f"{technical_version}:{qfq_source}:{actual_date.isoformat()}"
            ),
            industry_chain=industry_version,
        ),
        candidates=prepared,
        reasons=[
            *universe.reasons,
            *industry_reasons,
        ],
    )


def load_position_observations(
    path: Path,
    *,
    daily_inputs: CoreStrategyDailyInputs,
    positions: list[HoldingPosition],
) -> PreparedPositionObservations:
    """
    为所有已有持仓生成同日退出和延展观察，
    不要求其仍在候选榜。
    """

    observations: list[PositionObservation] = []
    unavailable: list[str] = []
    for position in positions:
        raw = _read_raw_bar(
            path,
            code=position.code,
            actual_date=daily_inputs.actual_date,
        )
        seed = PreliminaryCandidateSeed(
            code=position.code,
            name=position.name,
            route="pure_technical",
            base_grade="C",
            preliminary_rank=1,
            technical_rankings=[],
            industry_selection_refs=[],
        )
        technical = _read_technical_analysis(
            path,
            seed=seed,
            actual_date=daily_inputs.actual_date,
            technical_version=daily_inputs.technical_version,
            qfq_source=daily_inputs.qfq_source,
            corporate_actions_complete=(
                daily_inputs.corporate_actions_complete
            ),
        )
        if raw is None or technical is None:
            unavailable.append(position.code)
            continue
        observations.append(
            PositionObservation(
                code=position.code,
                open=raw.open,
                high=raw.high,
                low=raw.low,
                close=raw.close,
                limit_down_price=_main_board_limit_price(
                    raw.previous_close,
                    multiplier="0.90",
                ),
                suspended=raw.volume == 0,
                technical_signals_complete=technical.status == "ready",
                strong_bearish=technical.strong_bearish_veto,
                standard_rsi_top_divergence=(
                    technical.standard_rsi_top_divergence_active
                ),
                weak_bearish_rsi_composite=False,
                valid_volume_breakout=technical.valid_volume_breakout,
                strong_bullish_continuation=(
                    technical.strong_bullish_continuation
                ),
                new_stop_price=(
                    technical.stop_price
                    if technical.valid_volume_breakout
                    else None
                ),
                new_pressure_target=(
                    technical.pressure_target
                    if technical.valid_volume_breakout
                    else None
                ),
                latest_support_line=(
                    technical.supports[0].lower
                    if technical.supports
                    else None
                ),
            )
        )
    return PreparedPositionObservations(
        actual_date=daily_inputs.actual_date,
        observations=observations,
        unavailable_codes=unavailable,
    )


def read_strategy_opinion_evidence(
    path: Path,
    *,
    actual_date: date,
    candidate_codes: list[str],
    targeted_codes: list[str],
    rules_version: str = OPINION_PROMPT_VERSION,
) -> list[StrategyOpinionEvidence]:
    """
    读取新浪主源的最短完整 1/3/7 日窗口，不混合窗口。

    采集未完成或相关样本不足时不形成方向。
    """

    targeted = set(targeted_codes)
    results: list[StrategyOpinionEvidence] = []
    for code in candidate_codes:
        if code not in targeted:
            results.append(
                StrategyOpinionEvidence(
                    code=code,
                    targeted=False,
                    status="not_targeted",
                )
            )
            continue
        windows: list[PublicOpinionWindow] = []
        published: PublicOpinionWindow | None = None
        for days in (1, 3, 7):
            window = read_public_opinion_window(
                path,
                platform="sina",
                code=code,
                end_date=actual_date,
                days=days,
                rules_version=rules_version,
            )
            windows.append(window)
            if window.direction_status == "published":
                published = window
                break
        if published is not None:
            results.append(
                _published_opinion_evidence(
                    published,
                    rules_version=rules_version,
                )
            )
            continue
        status: Literal["collecting", "insufficient_sample"] = (
            "insufficient_sample"
            if any(
                window.direction_status == "insufficient_sample"
                for window in windows
            )
            else "collecting"
        )
        results.append(
            StrategyOpinionEvidence(
                code=code,
                targeted=True,
                status=status,
                platform="sina",
                rules_version=rules_version,
            )
        )
    return results


def _published_opinion_evidence(
    window: PublicOpinionWindow,
    *,
    rules_version: str,
) -> StrategyOpinionEvidence:
    assert window.direction is not None
    days = window.expected_day_count
    if days not in {1, 3, 7}:
        raise ValueError("策略舆论只接受 1、3、7 日窗口")
    return StrategyOpinionEvidence(
        code=window.code,
        targeted=True,
        status=window.direction,
        platform="sina",
        window_days=cast(Literal[1, 3, 7], days),
        direction_index=window.direction_index,
        rules_version=rules_version,
    )


def _technical_publication(
    path: Path,
    *,
    actual_date: date,
) -> tuple[str, str]:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT version, qfq_source
            FROM technical_score_publications
            WHERE official_start <= ? AND official_end >= ?
            ORDER BY published_at DESC, version DESC
            LIMIT 1
            """,
            (
                actual_date.isoformat(),
                actual_date.isoformat(),
            ),
        ).fetchone()
    if row is None:
        raise StrategyInputUnavailable("同日技术评分正式版本不可用")
    return cast(str, row[0]), cast(str, row[1])


def _require_candle_publication(
    path: Path,
    *,
    actual_date: date,
    qfq_source: str,
) -> None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM candle_dataset_publications
            WHERE actual_data_date = ? AND qfq_source = ?
            """,
            (actual_date.isoformat(), qfq_source),
        ).fetchone()
    if row is None:
        raise StrategyInputUnavailable("同日完整前复权 K 线尚未发布")


def _read_technical_rankings(
    path: Path,
    *,
    actual_date: date,
    technical_version: str,
    qfq_source: str,
) -> list[TechnicalRankingEvidence]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            WITH ranked AS (
                SELECT
                    code,
                    name,
                    board,
                    structure_score,
                    breakout_score,
                    relative_strength_score,
                    turnover_score,
                    DENSE_RANK() OVER (
                        ORDER BY structure_score DESC
                    ) AS structure_rank,
                    DENSE_RANK() OVER (
                        ORDER BY breakout_score DESC, turnover_score DESC
                    ) AS breakout_rank,
                    DENSE_RANK() OVER (
                        ORDER BY relative_strength_score DESC,
                                 turnover_score DESC
                    ) AS relative_strength_rank
                FROM technical_daily_scores
                WHERE version = ?
                  AND qfq_source = ?
                  AND actual_data_date = ?
            )
            SELECT
                code, name, board,
                structure_score, breakout_score,
                relative_strength_score, turnover_score,
                structure_rank, breakout_rank, relative_strength_rank
            FROM ranked
            WHERE structure_rank <= 20
               OR breakout_rank <= 20
               OR relative_strength_rank <= 20
            ORDER BY code
            """,
            (
                technical_version,
                qfq_source,
                actual_date.isoformat(),
            ),
        ).fetchall()
    if not rows:
        raise StrategyInputUnavailable("同日技术榜没有形成候选名次")
    result: list[TechnicalRankingEvidence] = []
    metrics: tuple[tuple[RankingMetric, int, int], ...] = (
        ("structure_score", 3, 7),
        ("breakout_score", 4, 8),
        ("relative_strength_score", 5, 9),
    )
    for row in rows:
        for metric, score_index, rank_index in metrics:
            rank = int(row[rank_index])
            if rank > 20:
                continue
            result.append(
                TechnicalRankingEvidence(
                    code=cast(str, row[0]),
                    name=cast(str, row[1]),
                    board=cast(Board, row[2]),
                    metric=metric,
                    market_rank=rank,
                    score=float(row[score_index]),
                    turnover_score=float(row[6]),
                )
            )
    return result


def _read_industry_candidates(
    path: Path,
    *,
    actual_date: date,
) -> tuple[list[IndustryChainCandidateEvidence], str | None, bool]:
    try:
        with open_database_connection(path) as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT selection_id, MAX(selection_version) AS version
                    FROM industry_chain_selections
                    WHERE status = 'selected'
                      AND substr(published_at, 1, 10) <= ?
                    GROUP BY selection_id
                )
                SELECT
                    selection.selection_id,
                    selection.selection_version,
                    selection.snapshot_sha256,
                    selection.snapshot_json
                FROM industry_chain_selections AS selection
                JOIN latest
                  ON latest.selection_id = selection.selection_id
                 AND latest.version = selection.selection_version
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM industry_chain_selection_dismissals AS dismissal
                    WHERE dismissal.selection_id = selection.selection_id
                      AND dismissal.selection_version =
                          selection.selection_version
                      AND substr(dismissal.dismissed_at, 1, 10) <= ?
                )
                ORDER BY selection.published_at, selection.selection_id
                """,
                (
                    actual_date.isoformat(),
                    actual_date.isoformat(),
                ),
            ).fetchall()
    except sqlite3.OperationalError:
        return [], None, False

    candidates: list[IndustryChainCandidateEvidence] = []
    version_parts: list[str] = []
    for row in rows:
        selection_ref = f"{row[0]}:{row[1]}"
        version_parts.append(f"{selection_ref}:{row[2]}")
        snapshot = cast(
            dict[str, object],
            json.loads(cast(str, row[3])),
        )
        raw_candidates = snapshot.get("candidates")
        if not isinstance(raw_candidates, list):
            continue
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            code = raw.get("code")
            name = raw.get("name")
            if not isinstance(code, str) or not isinstance(name, str):
                continue
            candidates.append(
                IndustryChainCandidateEvidence(
                    code=code,
                    name=name,
                    selection_ref=selection_ref,
                )
            )
    version = (
        "industry-chain:"
        + hashlib.sha256("|".join(version_parts).encode()).hexdigest()
        if version_parts
        else "industry-chain:empty"
    )
    return candidates, version, True


def _eligible_industry_candidates(
    path: Path,
    *,
    actual_date: date,
    candidates: list[IndustryChainCandidateEvidence],
) -> tuple[list[IndustryChainCandidateEvidence], list[str]]:
    codes = sorted(
        {
            item.code
            for item in candidates
            if _is_main_board_code(item.code)
        }
    )
    if not codes:
        return [], []
    placeholders = ",".join("?" for _ in codes)
    with open_database_connection(path) as connection:
        rows = connection.execute(
            f"""
            SELECT code, name
            FROM historical_security_facts
            WHERE source = ?
              AND actual_data_date = ?
              AND code IN ({placeholders})
              AND listing_trading_days >= 60
              AND volume > 0
            """,
            (
                HISTORY_SOURCE,
                actual_date.isoformat(),
                *codes,
            ),
        ).fetchall()
    current_names = {
        cast(str, row[0]): cast(str, row[1])
        for row in rows
        if not _excluded_name(cast(str, row[1]))
    }
    eligible = [
        item.model_copy(
            update={"name": current_names[item.code]}
        )
        for item in candidates
        if item.code in current_names
    ]
    eligible_keys = {
        (item.code, item.selection_ref)
        for item in eligible
    }
    reasons = [
        f"industry_candidate_ineligible:{item.code}"
        for item in candidates
        if (item.code, item.selection_ref) not in eligible_keys
    ]
    return eligible, list(dict.fromkeys(reasons))


def _prepare_candidate(
    path: Path,
    *,
    seed: PreliminaryCandidateSeed,
    actual_date: date,
    technical_version: str,
    qfq_source: str,
    corporate_actions_complete: bool,
) -> PreparedStrategyCandidate | None:
    raw_bar = _read_raw_bar(
        path,
        code=seed.code,
        actual_date=actual_date,
    )
    if raw_bar is None:
        return None
    technical = _read_technical_analysis(
        path,
        seed=seed,
        actual_date=actual_date,
        technical_version=technical_version,
        qfq_source=qfq_source,
        corporate_actions_complete=corporate_actions_complete,
    )
    permission = (
        technical is not None
        and technical.status == "ready"
        and bool(technical.entry_triggers)
        and not technical.strong_bearish_veto
    )
    reasons = []
    if technical is None:
        reasons.append("technical_history_incomplete")
    elif technical.status != "ready":
        reasons.append(technical.status)
    return PreparedStrategyCandidate(
        preliminary=PreliminaryCandidate(
            code=seed.code,
            name=seed.name,
            route=seed.route,
            base_grade=seed.base_grade,
            preliminary_rank=seed.preliminary_rank,
            technical_trade_permission=permission,
        ),
        technical=technical,
        technical_rankings=seed.technical_rankings,
        industry_selection_refs=seed.industry_selection_refs,
        raw_bar=raw_bar,
        input_reasons=reasons,
    )


def _read_raw_bar(
    path: Path,
    *,
    code: str,
    actual_date: date,
) -> RawDailyBar | None:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT open, high, low, close, previous_close, change_pct,
                   volume, turnover_cny, listing_trading_days, name
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ? AND code = ?
            """,
            (HISTORY_SOURCE, actual_date.isoformat(), code),
        ).fetchone()
    if (
        row is None
        or int(row[6]) <= 0
        or int(row[8]) < 60
        or _excluded_name(cast(str, row[9]))
    ):
        return None
    return RawDailyBar(
        open=float(row[0]),
        high=float(row[1]),
        low=float(row[2]),
        close=float(row[3]),
        previous_close=float(row[4]),
        change_pct=float(row[5]),
        volume=int(row[6]),
        turnover_cny=int(row[7]),
        listing_trading_days=int(row[8]),
    )


def _read_technical_analysis(
    path: Path,
    *,
    seed: PreliminaryCandidateSeed,
    actual_date: date,
    technical_version: str,
    qfq_source: str,
    corporate_actions_complete: bool,
) -> DailyTechnicalAnalysis | None:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT
                fact.actual_data_date,
                fact.open,
                fact.high,
                fact.low,
                fact.close,
                fact.volume,
                fact.turnover_cny,
                fact.listing_trading_days,
                score.derivative_state
            FROM historical_security_facts AS fact
            JOIN technical_daily_scores AS score
              ON score.version = ?
             AND score.qfq_source = fact.source
             AND score.actual_data_date = fact.actual_data_date
             AND score.code = fact.code
            WHERE fact.source = ?
              AND fact.code = ?
              AND fact.actual_data_date <= ?
            ORDER BY fact.actual_data_date DESC
            LIMIT 250
            """,
            (
                technical_version,
                qfq_source,
                seed.code,
                actual_date.isoformat(),
            ),
        ).fetchall()
    if (
        len(rows) < 60
        or date.fromisoformat(cast(str, rows[0][0])) != actual_date
    ):
        return None
    ordered = list(reversed(rows))
    bars = [
        DailyTechnicalBar(
            date=date.fromisoformat(cast(str, row[0])),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=int(row[5]),
            turnover_cny=int(row[6]),
            derivative_state=cast(DerivativeState, row[8]),
            suspended=int(row[5]) == 0,
            corporate_action=False,
        )
        for row in ordered
    ]
    return analyze_daily_technical(
        DailyTechnicalInput(
            code=seed.code,
            name=seed.name,
            actual_date=actual_date,
            qfq_source=qfq_source,
            listing_trading_days=int(rows[0][7]),
            corporate_actions_complete=corporate_actions_complete,
            bars=bars,
        )
    )


def _is_main_board_code(code: str) -> bool:
    return code.startswith(
        (
            "000",
            "001",
            "002",
            "003",
            "600",
            "601",
            "603",
            "605",
        )
    )


def _excluded_name(name: str) -> bool:
    return "ST" in name.upper() or "退" in name


def _main_board_limit_price(
    previous_close: float,
    *,
    multiplier: str,
) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    return float(
        (
            Decimal(str(previous_close))
            * Decimal(multiplier)
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
