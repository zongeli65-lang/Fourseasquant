from datetime import date, timedelta

import pytest

from fourseasquant.core_strategy_technical import (
    DailyTechnicalAnalysis,
    DailyTechnicalBar,
    DailyTechnicalInput,
    analyze_daily_technical,
)
from fourseasquant.technical_scoring import DerivativeState


START = date(2026, 1, 1)


def _bar(
    index: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1_000,
    turnover_cny: int = 10_000,
    derivative_state: DerivativeState = "zero",
) -> DailyTechnicalBar:
    return DailyTechnicalBar(
        date=START + timedelta(days=index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover_cny=turnover_cny,
        derivative_state=derivative_state,
    )


def _baseline(size: int = 60) -> list[DailyTechnicalBar]:
    bars: list[DailyTechnicalBar] = []
    for index in range(size):
        center = 100 + (0.15 if index % 4 < 2 else -0.15)
        bullish = index % 2 == 0
        open_price = center - 0.2 if bullish else center + 0.2
        close = center + 0.2 if bullish else center - 0.2
        bars.append(
            _bar(
                index,
                open_price=open_price,
                high=max(open_price, close) + 0.35,
                low=min(open_price, close) - 0.35,
                close=close,
            )
        )
    return bars


def _analyze(
    bars: list[DailyTechnicalBar],
    *,
    listing_trading_days: int | None = None,
    corporate_actions_complete: bool = True,
) -> DailyTechnicalAnalysis:
    return analyze_daily_technical(
        DailyTechnicalInput(
            code="600000",
            name="浦发银行",
            actual_date=bars[-1].date,
            qfq_source="test-qfq",
            listing_trading_days=(
                len(bars)
                if listing_trading_days is None
                else listing_trading_days
            ),
            corporate_actions_complete=corporate_actions_complete,
            bars=bars,
        )
    )


def test_less_than_sixty_listing_days_never_emits_signal() -> None:
    analysis = _analyze(_baseline(59), listing_trading_days=59)

    assert analysis.status == "insufficient_history"
    assert analysis.entry_triggers == []
    assert analysis.candlesticks == []


def test_incomplete_corporate_actions_block_signal_analysis() -> None:
    analysis = _analyze(
        _baseline(),
        corporate_actions_complete=False,
    )

    assert analysis.status == "corporate_actions_incomplete"
    assert analysis.entry_triggers == []


def test_bullish_engulfing_in_negative_derivative_context_is_strong_entry() -> None:
    bars = _baseline()
    bars[57] = bars[57].model_copy(
        update={"derivative_state": "negative"}
    )
    bars[58] = _bar(
        58,
        open_price=100.5,
        high=100.8,
        low=99.2,
        close=99.5,
        derivative_state="negative",
    )
    bars[59] = _bar(
        59,
        open_price=99.3,
        high=101,
        low=99.1,
        close=100.8,
        derivative_state="positive",
    )

    analysis = _analyze(bars)

    current = [
        item
        for item in analysis.candlesticks
        if item.completed_on == bars[-1].date
    ]
    assert any(item.pattern == "bullish_engulfing" for item in current)
    assert "strong_bullish_candlestick" in analysis.entry_triggers
    assert any(
        evidence.startswith("candlestick:bullish_engulfing")
        for evidence in analysis.strong_evidence_ids
    )


def test_same_geometry_in_zero_derivative_context_has_no_reversal_permission() -> None:
    bars = _baseline()
    bars[58] = _bar(
        58,
        open_price=100.5,
        high=100.8,
        low=99.2,
        close=99.5,
    )
    bars[59] = _bar(
        59,
        open_price=99.3,
        high=101,
        low=99.1,
        close=100.8,
    )

    analysis = _analyze(bars)

    assert "strong_bullish_candlestick" not in analysis.entry_triggers
    assert not any(
        item.pattern == "bullish_engulfing"
        for item in analysis.candlesticks
    )


def test_hammer_remains_weak_and_cannot_independently_trigger_strong_entry() -> None:
    bars = _baseline()
    bars[58] = bars[58].model_copy(
        update={"derivative_state": "negative"}
    )
    bars[59] = _bar(
        59,
        open_price=100,
        high=100.1,
        low=97,
        close=100.05,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    assert any(
        item.pattern == "hammer" and item.strength == "weak"
        for item in analysis.candlesticks
    )
    assert "strong_bullish_candlestick" not in analysis.entry_triggers


def test_bearish_engulfing_activates_five_day_bearish_veto() -> None:
    bars = _baseline()
    bars[57] = bars[57].model_copy(
        update={"derivative_state": "positive"}
    )
    bars[58] = _bar(
        58,
        open_price=99.5,
        high=100.7,
        low=99.3,
        close=100.5,
        derivative_state="positive",
    )
    bars[59] = _bar(
        59,
        open_price=100.7,
        high=100.9,
        low=99,
        close=99.2,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    assert any(
        item.pattern == "bearish_engulfing"
        for item in analysis.candlesticks
    )
    assert analysis.strong_bearish_veto is True


def test_unified_loose_doji_standard_is_used() -> None:
    bars = _baseline()
    bars[59] = _bar(
        59,
        open_price=100,
        high=101,
        low=99,
        close=100.02,
    )

    analysis = _analyze(bars)

    assert any(
        item.pattern == "doji"
        for item in analysis.candlesticks
        if item.completed_on == bars[-1].date
    )


def test_any_nonoverlapping_shadow_ranges_form_window_and_midpoint_level() -> None:
    bars = _baseline()
    bars[58] = _bar(
        58,
        open_price=99.8,
        high=100.2,
        low=99.5,
        close=100,
        derivative_state="positive",
    )
    bars[59] = _bar(
        59,
        open_price=100.25,
        high=100.8,
        low=100.21,
        close=100.6,
        derivative_state="positive",
    )

    analysis = _analyze(bars)

    assert any(
        item.pattern == "up_window"
        for item in analysis.candlesticks
    )
    window_levels = [
        level
        for level in analysis.supports
        if "up_window" in level.sources
    ]
    assert window_levels
    assert window_levels[0].center == pytest.approx(100.205)
    assert "strong_bullish_candlestick" not in analysis.entry_triggers


def test_completed_rising_three_methods_is_strong_continuation_entry() -> None:
    bars = _baseline()
    bars[54] = bars[54].model_copy(
        update={"derivative_state": "positive"}
    )
    bars[55] = _bar(
        55,
        open_price=99,
        high=102.2,
        low=98.8,
        close=102,
        volume=2_000,
        derivative_state="positive",
    )
    bars[56] = _bar(
        56,
        open_price=101.7,
        high=101.9,
        low=101.2,
        close=101.4,
        volume=500,
        derivative_state="positive",
    )
    bars[57] = _bar(
        57,
        open_price=101.4,
        high=101.6,
        low=100.9,
        close=101.1,
        volume=450,
        derivative_state="positive",
    )
    bars[58] = _bar(
        58,
        open_price=101.1,
        high=101.3,
        low=100.7,
        close=100.9,
        volume=500,
        derivative_state="positive",
    )
    bars[59] = _bar(
        59,
        open_price=101,
        high=103.3,
        low=100.8,
        close=103,
        volume=2_200,
        derivative_state="positive",
    )

    analysis = _analyze(bars)

    assert any(
        item.pattern == "rising_three_methods"
        and item.strength == "strong"
        for item in analysis.candlesticks
    )
    assert analysis.strong_bullish_continuation is True
    assert "strong_bullish_candlestick" in analysis.entry_triggers


def test_recent_confirmed_swing_low_forms_support_and_retest_stop() -> None:
    bars = _baseline()
    for index in range(51, 59):
        low = 95 if index == 55 else 98 + abs(index - 55) * 0.2
        close = low + 1
        bars[index] = _bar(
            index,
            open_price=close + 0.2,
            high=close + 0.5,
            low=low,
            close=close,
            derivative_state="negative",
        )
    bars[59] = _bar(
        59,
        open_price=96.2,
        high=96.5,
        low=94.9,
        close=96,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    swing_supports = [
        level
        for level in analysis.supports
        if "swing_low" in level.sources
    ]
    assert swing_supports
    assert "support_retest" in analysis.entry_triggers
    assert analysis.stop_price is not None
    assert analysis.stop_price < swing_supports[0].lower


def test_volume_and_turnover_must_both_confirm_pressure_breakout() -> None:
    bars = _baseline()
    for index in range(36, 45):
        high = 105 if index == 40 else 102 - abs(index - 40) * 0.1
        close = high - 1
        bars[index] = _bar(
            index,
            open_price=close - 0.2,
            high=high,
            low=close - 0.5,
            close=close,
            derivative_state="positive",
        )
    bars[59] = _bar(
        59,
        open_price=105,
        high=107,
        low=104.8,
        close=106.5,
        volume=2_000,
        turnover_cny=20_000,
        derivative_state="positive",
    )

    confirmed = _analyze(bars)
    bars[59] = bars[59].model_copy(update={"turnover_cny": 10_000})
    missing_turnover = _analyze(bars)

    assert confirmed.valid_volume_breakout is True
    assert missing_turnover.valid_volume_breakout is False
    assert confirmed.supports
    assert confirmed.supports[0].formed_on == confirmed.actual_date


def test_rsi_divergence_has_no_fixed_sixty_day_maximum() -> None:
    bars = _baseline(135)
    for index in range(20, 31):
        close = 100 - (index - 20) * 2
        bars[index] = _bar(
            index,
            open_price=close + 0.8,
            high=close + 1,
            low=close - 0.5,
            close=close,
            derivative_state="negative",
        )
    for index in range(31, 51):
        close = 80 + (index - 30)
        bars[index] = _bar(
            index,
            open_price=close - 0.4,
            high=close + 0.6,
            low=close - 0.6,
            close=close,
            derivative_state="positive",
        )
    slow_close = 100.0
    pullback_cycle = (-0.25, -0.25, 0.20, -0.70)
    for index in range(51, 133):
        slow_close += pullback_cycle[
            (index - 51) % len(pullback_cycle)
        ]
        bars[index] = _bar(
            index,
            open_price=slow_close + 0.15,
            high=slow_close + 0.4,
            low=slow_close - 0.4,
            close=slow_close,
            derivative_state="negative",
        )
    bars[133] = _bar(
        133,
        open_price=79.45,
        high=79.7,
        low=79.0,
        close=79.2,
        derivative_state="negative",
    )
    bars[134] = _bar(
        134,
        open_price=79.1,
        high=79.6,
        low=78.9,
        close=79.45,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    bullish = [
        item
        for item in analysis.rsi_divergences
        if item.direction == "bullish"
        and item.historical_pivot_on <= bars[30].date
        and (item.formed_on - item.historical_pivot_on).days > 60
    ]
    assert bullish
    assert any(item.strength == "standard" for item in bullish)
    assert any(
        item.confirmation == "candlestick"
        and item.status == "confirmed"
        for item in bullish
    )


def test_two_independent_weak_bullish_reversals_confirm_composite_entry() -> None:
    bars = _baseline()
    for index in (55, 58):
        bars[index] = bars[index].model_copy(
            update={"derivative_state": "negative"}
        )
    for index in (56, 59):
        bars[index] = _bar(
            index,
            open_price=100,
            high=100.25,
            low=99,
            close=100.2,
            derivative_state="negative",
        )

    analysis = _analyze(bars)

    hammers = [
        item
        for item in analysis.candlesticks
        if item.pattern == "hammer"
        and item.completed_on in {bars[56].date, bars[59].date}
    ]
    assert len(hammers) == 2
    assert "confirmed_bullish_composite" in analysis.entry_triggers
    assert any(
        item.startswith("composite:weak-candles:bullish")
        for item in analysis.strong_evidence_ids
    )


def test_confirmed_harami_stays_weak_without_independent_confirmation() -> None:
    bars = _baseline()
    bars[56] = bars[56].model_copy(
        update={"derivative_state": "negative"}
    )
    bars[57] = _bar(
        57,
        open_price=102,
        high=102.2,
        low=98.8,
        close=99,
        derivative_state="negative",
    )
    bars[58] = _bar(
        58,
        open_price=99.5,
        high=99.9,
        low=99.3,
        close=99.7,
        derivative_state="negative",
    )
    bars[59] = _bar(
        59,
        open_price=102.1,
        high=102.7,
        low=102,
        close=102.5,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    signal = next(
        item
        for item in analysis.candlesticks
        if item.pattern == "bullish_harami_confirmed"
    )
    assert signal.strength == "weak"
    assert not any(
        evidence.startswith(
            "candlestick:bullish_harami_confirmed"
        )
        for evidence in analysis.strong_evidence_ids
    )


def test_high_price_gapping_play_is_strong_continuation_entry() -> None:
    bars = _baseline()
    bars[54] = bars[54].model_copy(
        update={"derivative_state": "positive"}
    )
    bars[55] = _bar(
        55,
        open_price=100,
        high=103.2,
        low=99.8,
        close=103,
        volume=2_000,
        derivative_state="positive",
    )
    for index, center in ((56, 102.3), (57, 102.4), (58, 102.35)):
        bars[index] = _bar(
            index,
            open_price=center - 0.1,
            high=center + 0.25,
            low=center - 0.25,
            close=center + 0.1,
            volume=700,
            derivative_state="positive",
        )
    bars[59] = _bar(
        59,
        open_price=102.9,
        high=104.2,
        low=102.7,
        close=104,
        volume=2_100,
        derivative_state="positive",
    )

    analysis = _analyze(bars)

    signal = next(
        item
        for item in analysis.candlesticks
        if item.pattern == "high_price_gapping_play"
    )
    assert signal.strength == "strong"
    assert signal.function == "continuation"
    assert "strong_bullish_candlestick" in analysis.entry_triggers
    assert analysis.strong_bullish_continuation is True


def test_belt_hold_is_weak_and_has_no_native_strong_evidence() -> None:
    bars = _baseline()
    bars[58] = bars[58].model_copy(
        update={"derivative_state": "negative"}
    )
    bars[59] = _bar(
        59,
        open_price=99,
        high=102.2,
        low=98.95,
        close=102,
        derivative_state="negative",
    )

    analysis = _analyze(bars)

    signal = next(
        item
        for item in analysis.candlesticks
        if item.pattern == "bullish_belt_hold"
    )
    assert signal.strength == "weak"
    assert not any(
        evidence.startswith("candlestick:bullish_belt_hold")
        for evidence in analysis.strong_evidence_ids
    )


def test_two_confirmed_rising_lows_form_projected_trendline_support() -> None:
    bars = _baseline(80)
    for pivot_index, pivot_low in ((35, 95.0), (50, 96.0)):
        for offset in range(-3, 4):
            index = pivot_index + offset
            low = pivot_low + abs(offset) * 0.55
            close = low + 1.1
            bars[index] = _bar(
                index,
                open_price=close + 0.15,
                high=close + 0.55,
                low=low,
                close=close,
                derivative_state="negative",
            )
    for index in range(54, 80):
        close = 98.5 + (index - 54) * 0.08
        bars[index] = _bar(
            index,
            open_price=close - 0.1,
            high=close + 0.4,
            low=close - 0.4,
            close=close,
            derivative_state="positive",
        )

    analysis = _analyze(bars)

    trendline = next(
        level
        for level in analysis.supports
        if "ascending_trendline" in level.sources
    )
    assert trendline.slope_per_trading_day > 0
    assert trendline.center == pytest.approx(
        96 + (96 - 95) / (50 - 35) * (79 - 50)
    )


def test_failed_upward_breakout_becomes_new_resistance_boundary() -> None:
    bars = _baseline(80)
    for index in range(36, 45):
        high = 105 if index == 40 else 102 - abs(index - 40) * 0.1
        close = high - 1
        bars[index] = _bar(
            index,
            open_price=close - 0.2,
            high=high,
            low=close - 0.5,
            close=close,
            derivative_state="positive",
        )
    bars[50] = _bar(
        50,
        open_price=104.8,
        high=106,
        low=104.5,
        close=105.6,
        derivative_state="positive",
    )
    bars[51] = _bar(
        51,
        open_price=105,
        high=105.2,
        low=103.9,
        close=104.1,
        derivative_state="negative",
    )
    for index in range(52, 80):
        bars[index] = _bar(
            index,
            open_price=104.1,
            high=104.6,
            low=103.7,
            close=104.0,
            derivative_state="zero",
        )

    analysis = _analyze(bars)

    failed_boundary = next(
        level
        for level in analysis.resistances
        if "failed_breakout_high" in level.sources
    )
    assert failed_boundary.center == pytest.approx(106)
    assert failed_boundary.formed_on == bars[51].date


def test_historical_volume_break_converts_resistance_to_support() -> None:
    bars = _baseline(80)
    for index in range(36, 45):
        high = 105 if index == 40 else 102 - abs(index - 40) * 0.1
        close = high - 1
        bars[index] = _bar(
            index,
            open_price=close - 0.2,
            high=high,
            low=close - 0.5,
            close=close,
            derivative_state="positive",
        )
    bars[50] = _bar(
        50,
        open_price=105,
        high=107,
        low=104.8,
        close=106.5,
        volume=2_000,
        turnover_cny=20_000,
        derivative_state="positive",
    )
    for index in range(51, 80):
        close = 105.25
        bars[index] = _bar(
            index,
            open_price=105.15,
            high=105.55,
            low=104.85,
            close=close,
            derivative_state="positive",
        )

    analysis = _analyze(bars)

    converted = next(
        level
        for level in analysis.supports
        if "polarity_conversion" in level.sources
    )
    assert converted.center == pytest.approx(105)
    assert converted.formed_on == bars[50].date
