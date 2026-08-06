"""正式市场状态第八版的单一计算入口。

该模块把结构、历史自适应阈值、状态滞后、五指数蜡烛动能和上下文反转
封装在一个无输入输出接口之后。调用方只需提供日期完全对齐的五指数日线。
所有滚动门槛仅使用当日以前的数据，不含未来信息。
"""

from __future__ import annotations

from fourseasquant._market_regime_v8_adaptive import apply_adaptive_thresholds
from fourseasquant._market_regime_v8_contextual import (
    ContextualMomentumDay,
    apply_contextual_momentum_override,
)
from fourseasquant._market_regime_v8_hysteresis import apply_hysteresis
from fourseasquant._market_regime_v8_momentum import (
    calculate_candlestick_momentum,
    classify_momentum_phases,
)
from fourseasquant._market_regime_v8_structure import MarketBar
from fourseasquant._market_regime_v8_technical import (
    classify_market_technical_regime,
)

SH_SYMBOL = "sh000001"
INDEX_NAMES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}

# 第三版自适应快速层参数，也是第八版稳定基线的组成部分。
ADAPTIVE_LOOKBACK_DAYS = 120
ADAPTIVE_MINIMUM_HISTORY_DAYS = 40
ADAPTIVE_QUANTILE = 0.85
ADAPTIVE_MINIMUM_SCORE_THRESHOLD = 6.5
ADAPTIVE_MINIMUM_RELATIVE_THRESHOLD = 0.30
FAST_MEMORY_DAYS = 5

# 原始第八版上下文反转参数；不得与已否决的 0.60 ATR 尝试混用。
MINIMUM_PRIOR_STREAK = 4
PRIOR_MOVE_WINDOWS = (5, 10)
MINIMUM_PRIOR_MOVE_ATR = 4.0
DIRECTIONAL_EVENT_LOOKBACK = 5
MINIMUM_DIRECTIONAL_EVENTS = 3
CONFIRMATION_WINDOW_DAYS = 2
MINIMUM_REVERSAL_BODY_ATR = 0.50
MINIMUM_REVERSAL_BREADTH = 4
MINIMUM_ONE_ATR_BREADTH = 3


def calculate_market_regime_v8(
    index_bars: dict[str, list[MarketBar]],
) -> list[ContextualMomentumDay]:
    """计算原始第八版逐日状态，返回与输入相同长度的可审计结果。"""
    missing = set(INDEX_NAMES) - set(index_bars)
    if missing:
        names = "、".join(INDEX_NAMES[symbol] for symbol in sorted(missing))
        raise ValueError(f"第八版缺少主要指数：{names}")
    selected = {symbol: index_bars[symbol] for symbol in INDEX_NAMES}
    technical = classify_market_technical_regime(selected, sh_symbol=SH_SYMBOL)
    adaptive = apply_adaptive_thresholds(
        technical,
        lookback_days=ADAPTIVE_LOOKBACK_DAYS,
        minimum_history_days=ADAPTIVE_MINIMUM_HISTORY_DAYS,
        quantile=ADAPTIVE_QUANTILE,
        minimum_score_threshold=ADAPTIVE_MINIMUM_SCORE_THRESHOLD,
        minimum_relative_threshold=ADAPTIVE_MINIMUM_RELATIVE_THRESHOLD,
        fast_memory_days=FAST_MEMORY_DAYS,
    )
    baseline = apply_hysteresis(adaptive)
    momentum = classify_momentum_phases(
        calculate_candlestick_momentum(selected)
    )
    return apply_contextual_momentum_override(
        selected[SH_SYMBOL],
        baseline,
        momentum,
        minimum_prior_streak=MINIMUM_PRIOR_STREAK,
        prior_move_windows=PRIOR_MOVE_WINDOWS,
        minimum_prior_move_atr=MINIMUM_PRIOR_MOVE_ATR,
        directional_event_lookback=DIRECTIONAL_EVENT_LOOKBACK,
        minimum_directional_events=MINIMUM_DIRECTIONAL_EVENTS,
        confirmation_window_days=CONFIRMATION_WINDOW_DAYS,
        minimum_reversal_body_atr=MINIMUM_REVERSAL_BODY_ATR,
        minimum_reversal_breadth=MINIMUM_REVERSAL_BREADTH,
        minimum_one_atr_breadth=MINIMUM_ONE_ATR_BREADTH,
    )
