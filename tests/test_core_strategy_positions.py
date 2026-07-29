from datetime import date
from typing import Literal

import pytest

from fourseasquant.core_strategy_execution import FeeSchedule
from fourseasquant.core_strategy_positions import (
    ExitReason,
    HoldingPosition,
    PositionManagementDecision,
    PositionManagementInput,
    PositionObservation,
    manage_positions,
)


TARGET_DATE = date(2026, 7, 29)


def _position(
    *,
    code: str = "600000",
    shares: int = 1_000,
    cost_price: float = 10,
    stop_price: float = 9,
    pressure_target: float | None = 12,
    initial_risk: float = 1,
    highest_close_since_entry: float = 10,
    profit_protection_active: bool = False,
    final_profit_line: float | None = None,
    pending_exit_reason: ExitReason | None = None,
) -> HoldingPosition:
    return HoldingPosition(
        code=code,
        name=f"股票{code}",
        shares=shares,
        cost_price=cost_price,
        stop_price=stop_price,
        pressure_target=pressure_target,
        initial_risk=initial_risk,
        highest_close_since_entry=highest_close_since_entry,
        profit_protection_active=profit_protection_active,
        final_profit_line=final_profit_line,
        pending_exit_reason=pending_exit_reason,
    )


def _observation(
    *,
    code: str = "600000",
    open_price: float = 10,
    high: float = 10.5,
    low: float = 9.5,
    close: float = 10,
    limit_down_price: float = 9,
    suspended: bool = False,
    technical_signals_complete: bool = True,
    strong_bearish: bool = False,
    standard_rsi_top_divergence: bool = False,
    weak_bearish_rsi_composite: bool = False,
    valid_volume_breakout: bool = False,
    strong_bullish_continuation: bool = False,
    new_stop_price: float | None = None,
    new_pressure_target: float | None = None,
    latest_support_line: float | None = None,
) -> PositionObservation:
    return PositionObservation(
        code=code,
        open=open_price,
        high=high,
        low=low,
        close=close,
        limit_down_price=limit_down_price,
        suspended=suspended,
        technical_signals_complete=technical_signals_complete,
        strong_bearish=strong_bearish,
        standard_rsi_top_divergence=standard_rsi_top_divergence,
        weak_bearish_rsi_composite=weak_bearish_rsi_composite,
        valid_volume_breakout=valid_volume_breakout,
        strong_bullish_continuation=strong_bullish_continuation,
        new_stop_price=new_stop_price,
        new_pressure_target=new_pressure_target,
        latest_support_line=latest_support_line,
    )


def _manage(
    position: HoldingPosition,
    observation: PositionObservation | None,
    *,
    corporate_actions_complete: bool = True,
    fees: FeeSchedule | None = None,
) -> PositionManagementDecision:
    return manage_positions(
        PositionManagementInput(
            actual_date=TARGET_DATE,
            strategy_version="core-strategy-v1",
            corporate_actions_complete=corporate_actions_complete,
            positions=[position],
            observations=[] if observation is None else [observation],
            fees=fees or FeeSchedule(),
        )
    )


def test_close_below_stop_exits_without_volume_requirement() -> None:
    decision = _manage(
        _position(),
        _observation(
            open_price=9.2,
            high=9.4,
            low=8.5,
            close=8.9,
            limit_down_price=8.5,
        ),
    )

    assert decision.positions == []
    assert decision.orders[0].reason == "stop_close_break"
    assert decision.orders[0].execution_session == "close"


def test_shadow_break_below_stop_does_not_exit_when_close_recovers() -> None:
    decision = _manage(
        _position(),
        _observation(open_price=9.3, high=9.6, low=8.5, close=9.2),
    )

    assert decision.orders == []
    assert decision.positions[0].stop_price == 9


def test_pressure_touch_exits_at_close_without_holding_extension() -> None:
    decision = _manage(
        _position(pressure_target=12),
        _observation(high=12.1, low=10, close=11.8),
    )

    assert decision.orders[0].reason == "pressure_target_touched"
    assert decision.orders[0].execution_price == 11.8


def test_volume_breakout_extends_holding_and_accepts_new_levels() -> None:
    decision = _manage(
        _position(pressure_target=12),
        _observation(
            open_price=11.5,
            high=12.5,
            low=11,
            close=12.3,
            valid_volume_breakout=True,
            new_stop_price=11.8,
            new_pressure_target=15,
        ),
    )

    assert decision.orders == []
    updated = decision.positions[0]
    assert updated.stop_price == 11.8
    assert updated.pressure_target == 15


def test_strong_bullish_continuation_can_delay_pressure_exit() -> None:
    decision = _manage(
        _position(pressure_target=12),
        _observation(
            open_price=11.5,
            high=12.2,
            low=11,
            close=11.9,
            strong_bullish_continuation=True,
        ),
    )

    assert decision.orders == []
    assert decision.positions[0].pressure_target == 12


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("strong_bearish", "strong_bearish"),
        ("standard_rsi_top_divergence", "standard_rsi_top_divergence"),
        ("weak_bearish_rsi_composite", "weak_bearish_rsi_composite"),
    ],
)
def test_confirmed_bearish_evidence_exits_full_position(
    field: Literal[
        "strong_bearish",
        "standard_rsi_top_divergence",
        "weak_bearish_rsi_composite",
    ],
    reason: ExitReason,
) -> None:
    decision = _manage(
        _position(shares=1_050),
        _observation(
            strong_bearish=field == "strong_bearish",
            standard_rsi_top_divergence=(
                field == "standard_rsi_top_divergence"
            ),
            weak_bearish_rsi_composite=(
                field == "weak_bearish_rsi_composite"
            ),
        ),
    )

    assert decision.orders[0].shares == 1_050
    assert decision.orders[0].reason == reason


def test_profit_protection_activates_only_from_close_reaching_one_r() -> None:
    position = _position(pressure_target=None)

    intraday_only = _manage(
        position,
        _observation(high=11.5, low=9.5, close=10.8),
    )
    close_confirmed = _manage(
        position,
        _observation(high=11.5, low=10, close=11.2),
    )

    assert intraday_only.positions[0].profit_protection_active is False
    assert close_confirmed.positions[0].profit_protection_active is True
    assert close_confirmed.positions[0].final_profit_line == pytest.approx(11)


def test_close_below_final_profit_line_exits_after_high_close_retrace() -> None:
    position = _position(
        pressure_target=None,
        highest_close_since_entry=14,
        profit_protection_active=True,
        final_profit_line=12,
    )

    decision = _manage(
        position,
        _observation(
            open_price=12,
            high=12.2,
            low=11.5,
            close=11.9,
        ),
    )

    assert decision.orders[0].reason == "final_profit_protection"


def test_latest_support_can_raise_final_profit_line() -> None:
    position = _position(
        pressure_target=None,
        highest_close_since_entry=14,
        profit_protection_active=True,
        final_profit_line=12,
    )

    decision = _manage(
        position,
        _observation(
            open_price=12.9,
            high=13.2,
            low=12.5,
            close=13,
            latest_support_line=12.8,
        ),
    )

    assert decision.orders == []
    assert decision.positions[0].final_profit_line == pytest.approx(12.8)


def test_prior_pending_exit_sells_at_resumption_open_when_not_limit_down() -> None:
    decision = _manage(
        _position(pending_exit_reason="stop_close_break"),
        _observation(
            open_price=9.2,
            high=9.5,
            low=9,
            close=9.3,
            limit_down_price=9,
        ),
    )

    assert decision.orders[0].execution_session == "open"
    assert decision.orders[0].execution_price == 9.2


def test_prior_pending_exit_sells_at_close_after_limit_down_opens() -> None:
    decision = _manage(
        _position(pending_exit_reason="strong_bearish"),
        _observation(
            open_price=9,
            high=9.4,
            low=9,
            close=9.3,
            limit_down_price=9,
        ),
    )

    assert decision.orders[0].execution_session == "close"
    assert decision.orders[0].execution_price == 9.3


def test_prior_pending_exit_remains_when_open_and_close_are_limit_down() -> None:
    decision = _manage(
        _position(pending_exit_reason="strong_bearish"),
        _observation(
            open_price=9,
            high=9,
            low=9,
            close=9,
            limit_down_price=9,
        ),
    )

    assert decision.orders == []
    assert decision.positions[0].pending_exit_reason == "strong_bearish"


def test_same_day_signal_never_backfills_that_day_open() -> None:
    decision = _manage(
        _position(),
        _observation(
            open_price=10,
            high=10,
            low=9,
            close=9,
            limit_down_price=9,
            strong_bearish=True,
        ),
    )

    assert decision.orders == []
    assert decision.positions[0].pending_exit_reason == "strong_bearish"


def test_suspension_preserves_position_and_pending_exit() -> None:
    decision = _manage(
        _position(pending_exit_reason="stop_close_break"),
        _observation(suspended=True),
    )

    assert decision.orders == []
    assert decision.positions[0].pending_exit_reason == "stop_close_break"


def test_sell_fees_include_commission_transfer_and_stamp_tax() -> None:
    fees = FeeSchedule()
    decision = _manage(
        _position(shares=1_050),
        _observation(
            open_price=12,
            high=12,
            low=11,
            close=12,
            strong_bearish=True,
        ),
        fees=fees,
    )

    order = decision.orders[0]
    assert order.notional == pytest.approx(12_600)
    assert order.commission == pytest.approx(5)
    assert order.transfer_fee == pytest.approx(0.13)
    assert order.stamp_tax == pytest.approx(6.3)
    assert order.net_cash == pytest.approx(12_588.57)


def test_incomplete_corporate_actions_block_publication_and_all_orders() -> None:
    position = _position()
    decision = _manage(
        position,
        _observation(strong_bearish=True),
        corporate_actions_complete=False,
    )

    assert decision.publication_blocked is True
    assert decision.orders == []
    assert decision.positions == [position]


def test_missing_observation_preserves_position_without_fabricating_signal() -> None:
    position = _position()
    decision = _manage(position, None)

    assert decision.orders == []
    assert decision.positions == [position]
    assert decision.decisions[0].status == "data_missing"
