"""第八版正式内核：用五指数蜡烛动能阶段生成市场状态。

原型问题：不用固定形态名称和方向持有锁，能否把急涨、上冲失败、空头接管、
下跌衰竭、重新平衡和再度上升表示成可逐日审计的状态转换？

本模块纯计算、无输入输出，由正式市场状态编排入口调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from fourseasquant._market_regime_v8_structure import (
    MarketBar,
    TrendState,
    classify_market_structure,
)


MomentumPhase = Literal[
    "bullish_impulse",
    "bullish_exhaustion",
    "bearish_impulse",
    "bearish_exhaustion",
    "balance",
]
MomentumEvent = Literal[
    "bullish_shock",
    "bearish_shock",
    "top_exhaustion",
    "bottom_exhaustion",
    "bullish_impulse",
    "bearish_impulse",
    "balance",
    "none",
]


@dataclass(frozen=True)
class CandlestickMomentumEvidence:
    trading_date: date
    close_impulse_atr: float
    body_impulse_atr: float
    close_location: float
    upper_rejection_atr: float
    lower_rejection_atr: float
    advancing_count: int
    declining_count: int
    bullish_one_atr_count: int
    bearish_one_atr_count: int
    gap_up_bearish_count: int
    gap_down_bullish_count: int
    bullish_shock_threshold: float
    bearish_shock_threshold: float
    bullish_shock: bool
    bearish_shock: bool
    top_exhaustion: bool
    bottom_exhaustion: bool
    bullish_impulse: bool
    bearish_impulse: bool
    balance: bool


@dataclass(frozen=True)
class CandlestickMomentumStateDay:
    trading_date: date
    state: TrendState
    phase: MomentumPhase
    previous_phase: MomentumPhase
    event: MomentumEvent
    transition_candidate: Literal["bullish", "bearish"] | None
    transition_candidate_streak: int
    direction_lock_cancelled: bool
    evidence: CandlestickMomentumEvidence
    reason: str


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def calculate_candlestick_momentum(
    index_bars: dict[str, list[MarketBar]],
    *,
    lookback_days: int = 120,
    minimum_history_days: int = 40,
    shock_quantile: float = 0.975,
    minimum_shock_atr: float = 1.2,
    shock_breadth_count: int = 4,
    shock_directional_count: int = 5,
    impulse_atr: float = 0.80,
    exhaustion_body_atr: float = 0.50,
    rejection_atr: float = 0.75,
    balance_body_atr: float = 0.08,
) -> list[CandlestickMomentumEvidence]:
    """把每根K线转换为五指数同步动能证据，所有动态门槛只看此前历史。"""
    if len(index_bars) < 3:
        raise ValueError("至少需要三个指数")
    first_symbol = next(iter(index_bars))
    dates = [item.trading_date for item in index_bars[first_symbol]]
    if any([item.trading_date for item in bars] != dates for bars in index_bars.values()):
        raise ValueError("指数交易日不一致")

    structures = {
        symbol: classify_market_structure(bars)[0]
        for symbol, bars in index_bars.items()
    }
    raw_rows: list[dict[str, float | int | date]] = []
    for index, trading_date in enumerate(dates):
        if index == 0:
            raw_rows.append(
                {
                    "trading_date": trading_date,
                    "close_impulse_atr": 0.0,
                    "body_impulse_atr": 0.0,
                    "close_location": 0.0,
                    "upper_rejection_atr": 0.0,
                    "lower_rejection_atr": 0.0,
                    "advancing_count": 0,
                    "declining_count": 0,
                    "bullish_one_atr_count": 0,
                    "bearish_one_atr_count": 0,
                    "gap_up_bearish_count": 0,
                    "gap_down_bullish_count": 0,
                }
            )
            continue
        close_impulses: list[float] = []
        body_impulses: list[float] = []
        close_locations: list[float] = []
        upper_rejections: list[float] = []
        lower_rejections: list[float] = []
        gap_up_bearish_count = 0
        gap_down_bullish_count = 0
        for symbol, bars in index_bars.items():
            current = bars[index]
            previous = bars[index - 1]
            previous_atr = structures[symbol][index - 1].atr10
            price_range = max(current.high - current.low, current.close * 0.0001)
            close_impulse = (current.close - previous.close) / previous_atr
            body_impulse = (current.close - current.open) / previous_atr
            close_location = (
                2 * current.close - current.high - current.low
            ) / price_range
            upper_rejection = (
                current.high - max(current.open, current.close)
            ) / previous_atr
            lower_rejection = (
                min(current.open, current.close) - current.low
            ) / previous_atr
            close_impulses.append(close_impulse)
            body_impulses.append(body_impulse)
            close_locations.append(close_location)
            upper_rejections.append(upper_rejection)
            lower_rejections.append(lower_rejection)
            gap_up_bearish_count += int(
                current.open > previous.high and current.close < current.open
            )
            gap_down_bullish_count += int(
                current.open < previous.low and current.close > current.open
            )
        raw_rows.append(
            {
                "trading_date": trading_date,
                "close_impulse_atr": sum(close_impulses) / len(close_impulses),
                "body_impulse_atr": sum(body_impulses) / len(body_impulses),
                "close_location": sum(close_locations) / len(close_locations),
                "upper_rejection_atr": sum(upper_rejections) / len(upper_rejections),
                "lower_rejection_atr": sum(lower_rejections) / len(lower_rejections),
                "advancing_count": sum(value > 0 for value in close_impulses),
                "declining_count": sum(value < 0 for value in close_impulses),
                "bullish_one_atr_count": sum(value >= 1.0 for value in close_impulses),
                "bearish_one_atr_count": sum(value <= -1.0 for value in close_impulses),
                "gap_up_bearish_count": gap_up_bearish_count,
                "gap_down_bullish_count": gap_down_bullish_count,
            }
        )

    result: list[CandlestickMomentumEvidence] = []
    for index, raw in enumerate(raw_rows):
        history = raw_rows[max(0, index - lookback_days) : index]
        if len(history) >= minimum_history_days:
            bullish_history = [
                float(cast(float | int, item["close_impulse_atr"]))
                for item in history
                if float(cast(float | int, item["close_impulse_atr"])) > 0
            ]
            bearish_history = [
                -float(cast(float | int, item["close_impulse_atr"]))
                for item in history
                if float(cast(float | int, item["close_impulse_atr"])) < 0
            ]
            bullish_shock_threshold = max(
                minimum_shock_atr, _quantile(bullish_history, shock_quantile)
            )
            bearish_shock_threshold = max(
                minimum_shock_atr, _quantile(bearish_history, shock_quantile)
            )
        else:
            bullish_shock_threshold = minimum_shock_atr
            bearish_shock_threshold = minimum_shock_atr

        close_impulse = float(cast(float | int, raw["close_impulse_atr"]))
        body_impulse = float(cast(float | int, raw["body_impulse_atr"]))
        close_location = float(cast(float | int, raw["close_location"]))
        upper_rejection = float(cast(float | int, raw["upper_rejection_atr"]))
        lower_rejection = float(cast(float | int, raw["lower_rejection_atr"]))
        advancing_count = int(cast(float | int, raw["advancing_count"]))
        declining_count = int(cast(float | int, raw["declining_count"]))
        bullish_one_atr_count = int(
            cast(float | int, raw["bullish_one_atr_count"])
        )
        bearish_one_atr_count = int(
            cast(float | int, raw["bearish_one_atr_count"])
        )
        gap_up_bearish_count = int(
            cast(float | int, raw["gap_up_bearish_count"])
        )
        gap_down_bullish_count = int(
            cast(float | int, raw["gap_down_bullish_count"])
        )

        bullish_shock = (
            bullish_one_atr_count >= shock_breadth_count
            and advancing_count >= shock_directional_count
            and close_impulse >= bullish_shock_threshold
        )
        bearish_shock = (
            bearish_one_atr_count >= shock_breadth_count
            and declining_count >= shock_directional_count
            and -close_impulse >= bearish_shock_threshold
        )
        bullish_impulse = (
            advancing_count >= 4
            and close_impulse >= impulse_atr
            and body_impulse >= 0.10
            and close_location >= 0
        )
        bearish_impulse = (
            declining_count >= 4
            and close_impulse <= -impulse_atr
            and body_impulse <= -0.10
            and close_location <= 0
        )
        top_exhaustion = (
            gap_up_bearish_count >= 3 and body_impulse <= -exhaustion_body_atr
        ) or (
            advancing_count >= 3
            and upper_rejection >= rejection_atr
            and close_location <= 0
        )
        bottom_exhaustion = (
            gap_down_bullish_count >= 3 and body_impulse >= 0
        ) or (
            declining_count >= 3
            and lower_rejection >= rejection_atr
            and close_location >= 0
        )
        balance = (
            abs(body_impulse) <= balance_body_atr
            and abs(close_impulse) <= impulse_atr
        )
        result.append(
            CandlestickMomentumEvidence(
                trading_date=date.fromisoformat(str(raw["trading_date"])),
                close_impulse_atr=close_impulse,
                body_impulse_atr=body_impulse,
                close_location=close_location,
                upper_rejection_atr=upper_rejection,
                lower_rejection_atr=lower_rejection,
                advancing_count=advancing_count,
                declining_count=declining_count,
                bullish_one_atr_count=bullish_one_atr_count,
                bearish_one_atr_count=bearish_one_atr_count,
                gap_up_bearish_count=gap_up_bearish_count,
                gap_down_bullish_count=gap_down_bullish_count,
                bullish_shock_threshold=bullish_shock_threshold,
                bearish_shock_threshold=bearish_shock_threshold,
                bullish_shock=bullish_shock,
                bearish_shock=bearish_shock,
                top_exhaustion=top_exhaustion,
                bottom_exhaustion=bottom_exhaustion,
                bullish_impulse=bullish_impulse,
                bearish_impulse=bearish_impulse,
                balance=balance,
            )
        )
    return result


def classify_momentum_phases(
    evidence_days: list[CandlestickMomentumEvidence],
    *,
    ordinary_confirmation_days: int = 3,
) -> list[CandlestickMomentumStateDay]:
    """按动能控制权转换更新阶段；极端冲击优先于一切方向记忆。"""
    phase: MomentumPhase = "balance"
    transition_candidate: Literal["bullish", "bearish"] | None = None
    transition_candidate_streak = 0
    result: list[CandlestickMomentumStateDay] = []
    for item in evidence_days:
        previous_phase = phase
        event: MomentumEvent = "none"
        direction_lock_cancelled = False
        reason = "没有足以改变当前动能阶段的新证据"

        if phase == "bullish_impulse":
            transition_candidate = None
            transition_candidate_streak = 0
            if item.bearish_shock:
                phase = "bearish_impulse"
                event = "bearish_shock"
                direction_lock_cancelled = True
                reason = "跨指数极端负向冲击，空头立即接管"
            elif item.top_exhaustion:
                phase = "bullish_exhaustion"
                event = "top_exhaustion"
                direction_lock_cancelled = True
                reason = "上冲失败或高位拒绝，多头动能进入衰竭警戒"
            elif item.bullish_shock:
                event = "bullish_shock"
                reason = "跨指数极端正向冲击继续支持上行"
            elif item.bearish_impulse:
                phase = "bullish_exhaustion"
                event = "bearish_impulse"
                direction_lock_cancelled = True
                reason = "广泛负向动能出现，先解除上行控制并进入衰竭警戒"
            elif item.bullish_impulse:
                event = "bullish_impulse"
                reason = "正向实体、收盘位置与指数扩散继续支持上行"
        elif phase == "bullish_exhaustion":
            transition_candidate = None
            transition_candidate_streak = 0
            if item.bearish_shock:
                phase = "bearish_impulse"
                event = "bearish_shock"
                reason = "衰竭警戒后出现极端负向冲击，确认空头接管"
            elif item.bearish_impulse:
                phase = "bearish_impulse"
                event = "bearish_impulse"
                reason = "上冲衰竭后出现广泛负向冲量，确认转为下行"
            elif item.bullish_shock or item.bullish_impulse:
                phase = "bullish_impulse"
                event = "bullish_shock" if item.bullish_shock else "bullish_impulse"
                reason = "正向动能重新建立，解除顶部衰竭警戒"
            elif item.balance:
                phase = "balance"
                event = "balance"
                reason = "上冲衰竭后动能归于平衡，转为震荡"
        elif phase == "bearish_impulse":
            transition_candidate = None
            transition_candidate_streak = 0
            if item.bullish_shock:
                phase = "bullish_impulse"
                event = "bullish_shock"
                direction_lock_cancelled = True
                reason = "跨指数极端正向冲击，多头立即接管"
            elif item.bottom_exhaustion:
                phase = "bearish_exhaustion"
                event = "bottom_exhaustion"
                direction_lock_cancelled = True
                reason = "低位拒绝或下探收复，空头动能进入衰竭警戒"
            elif item.bearish_shock:
                event = "bearish_shock"
                reason = "跨指数极端负向冲击继续支持下行"
            elif item.bearish_impulse:
                event = "bearish_impulse"
                reason = "负向实体、收盘位置与指数扩散继续支持下行"
            elif item.bullish_impulse:
                phase = "bearish_exhaustion"
                event = "bullish_impulse"
                direction_lock_cancelled = True
                reason = "广泛正向动能出现，先解除下行控制并观察见底"
        elif phase == "bearish_exhaustion":
            transition_candidate = None
            transition_candidate_streak = 0
            if item.bullish_shock:
                phase = "bullish_impulse"
                event = "bullish_shock"
                reason = "衰竭警戒后出现极端正向冲击，多头立即接管"
            elif item.bearish_shock or item.bearish_impulse:
                phase = "bearish_impulse"
                event = "bearish_shock" if item.bearish_shock else "bearish_impulse"
                reason = "负向动能重新增强，见底尝试失败"
            elif item.bullish_impulse or item.balance:
                phase = "balance"
                event = "bullish_impulse" if item.bullish_impulse else "balance"
                reason = "空头不再延续，先转为震荡而不是直接判为上行"
        else:
            if item.bearish_shock:
                phase = "bearish_impulse"
                event = "bearish_shock"
                transition_candidate = None
                transition_candidate_streak = 0
                reason = "震荡中出现跨指数极端负向冲击，立即转为下行"
            elif item.bullish_shock:
                phase = "bullish_impulse"
                event = "bullish_shock"
                transition_candidate = None
                transition_candidate_streak = 0
                reason = "震荡中出现跨指数极端正向冲击，立即转为上行"
            elif item.bearish_impulse or item.bullish_impulse:
                candidate: Literal["bullish", "bearish"] = (
                    "bearish" if item.bearish_impulse else "bullish"
                )
                transition_candidate_streak = (
                    transition_candidate_streak + 1
                    if transition_candidate == candidate
                    else 1
                )
                transition_candidate = candidate
                event = "bearish_impulse" if candidate == "bearish" else "bullish_impulse"
                if transition_candidate_streak >= ordinary_confirmation_days:
                    phase = (
                        "bearish_impulse"
                        if candidate == "bearish"
                        else "bullish_impulse"
                    )
                    reason = f"震荡中{('负向' if candidate == 'bearish' else '正向')}动能连续两日建立，方向接管"
                    transition_candidate = None
                    transition_candidate_streak = 0
                else:
                    reason = f"震荡中出现首日{('负向' if candidate == 'bearish' else '正向')}动能，等待连续确认"
            elif item.balance:
                event = "balance"
                transition_candidate = None
                transition_candidate_streak = 0
                reason = "多空动能保持平衡"
            else:
                transition_candidate = None
                transition_candidate_streak = 0

        state: TrendState = (
            "rising"
            if phase in ("bullish_impulse", "bullish_exhaustion")
            else "falling"
            if phase in ("bearish_impulse", "bearish_exhaustion")
            else "sideways"
        )
        result.append(
            CandlestickMomentumStateDay(
                trading_date=item.trading_date,
                state=state,
                phase=phase,
                previous_phase=previous_phase,
                event=event,
                transition_candidate=transition_candidate,
                transition_candidate_streak=transition_candidate_streak,
                direction_lock_cancelled=direction_lock_cancelled,
                evidence=item,
                reason=reason,
            )
        )
    return result
