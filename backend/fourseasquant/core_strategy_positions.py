from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from fourseasquant.core_strategy_execution import FeeSchedule


ExitReason = Literal[
    "stop_close_break",
    "pressure_target_touched",
    "strong_bearish",
    "standard_rsi_top_divergence",
    "weak_bearish_rsi_composite",
    "final_profit_protection",
]
PositionStatus = Literal[
    "holding",
    "pending_exit",
    "exited",
    "suspended",
    "data_missing",
    "publication_blocked",
]
ExecutionSession = Literal["open", "close"]


class HoldingPosition(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    shares: int = Field(gt=0)
    cost_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    pressure_target: float | None = Field(default=None, gt=0)
    initial_risk: float = Field(gt=0)
    highest_close_since_entry: float = Field(gt=0)
    profit_protection_active: bool = False
    final_profit_line: float | None = Field(default=None, gt=0)
    pending_exit_reason: ExitReason | None = None

    @model_validator(mode="after")
    def validate_profit_protection(self) -> Self:
        if self.highest_close_since_entry < self.cost_price:
            raise ValueError("持仓以来最高收盘价不能低于成本价")
        if self.profit_protection_active and self.final_profit_line is None:
            raise ValueError("已激活利润保护时必须包含最终保护线")
        if not self.profit_protection_active and self.final_profit_line is not None:
            raise ValueError(
                "未激活利润保护时不能提前设置最终保护线"
            )
        return self


class PositionObservation(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    limit_down_price: float = Field(gt=0)
    suspended: bool = False
    technical_signals_complete: bool = True
    strong_bearish: bool = False
    standard_rsi_top_divergence: bool = False
    weak_bearish_rsi_composite: bool = False
    valid_volume_breakout: bool = False
    confirmed_pressure_breakout: bool = False
    strong_bullish_continuation: bool = False
    new_stop_price: float | None = Field(default=None, gt=0)
    new_pressure_target: float | None = Field(default=None, gt=0)
    latest_support_line: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_prices(self) -> Self:
        if self.high < max(self.open, self.close):
            raise ValueError("最高价不能低于开盘价或收盘价")
        if self.low > min(self.open, self.close):
            raise ValueError("最低价不能高于开盘价或收盘价")
        return self


class PositionManagementInput(BaseModel):
    """持仓退出和延展模块的唯一输入。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    corporate_actions_complete: bool
    positions: list[HoldingPosition]
    observations: list[PositionObservation]
    fees: FeeSchedule = Field(default_factory=FeeSchedule)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        position_codes = [position.code for position in self.positions]
        observation_codes = [
            observation.code for observation in self.observations
        ]
        if len(set(position_codes)) != len(position_codes):
            raise ValueError("持仓股票代码不能重复")
        if len(set(observation_codes)) != len(observation_codes):
            raise ValueError("持仓日频观察股票代码不能重复")
        unexpected = set(observation_codes) - set(position_codes)
        if unexpected:
            raise ValueError("持仓日频观察不能包含未持有股票")
        return self


class PositionDecision(BaseModel):
    code: str
    status: PositionStatus
    reason: ExitReason | None = None
    execution_session: ExecutionSession | None = None
    final_profit_line: float | None = None


class SimulatedSellOrder(BaseModel):
    actual_date: date
    strategy_version: str
    code: str
    name: str
    execution_session: ExecutionSession
    execution_price: float
    shares: int
    notional: float
    commission: float
    transfer_fee: float
    stamp_tax: float
    net_cash: float
    reason: ExitReason


class PositionManagementDecision(BaseModel):
    actual_date: date
    strategy_version: str
    publication_blocked: bool
    decisions: list[PositionDecision]
    positions: list[HoldingPosition]
    orders: list[SimulatedSellOrder]


def manage_positions(
    source: PositionManagementInput,
) -> PositionManagementDecision:
    """
    管理全部已有持仓。

    退出始终是全仓；
    当日收盘新信号只允许按收盘成交，
    跌停无法成交时转为待卖。
    只有前一交易日已经待卖的仓位，
    才可以在复牌或开板日按开盘规则成交。
    """

    if not source.corporate_actions_complete:
        return PositionManagementDecision(
            actual_date=source.actual_date,
            strategy_version=source.strategy_version,
            publication_blocked=True,
            decisions=[
                PositionDecision(
                    code=position.code,
                    status="publication_blocked",
                    reason=position.pending_exit_reason,
                    final_profit_line=position.final_profit_line,
                )
                for position in source.positions
            ],
            positions=[
                position.model_copy(deep=True)
                for position in source.positions
            ],
            orders=[],
        )

    observations = {
        observation.code: observation
        for observation in source.observations
    }
    surviving: list[HoldingPosition] = []
    decisions: list[PositionDecision] = []
    orders: list[SimulatedSellOrder] = []

    for original in source.positions:
        position = original.model_copy(deep=True)
        observation = observations.get(position.code)
        if observation is None:
            surviving.append(position)
            decisions.append(
                PositionDecision(
                    code=position.code,
                    status="data_missing",
                    reason=position.pending_exit_reason,
                    final_profit_line=position.final_profit_line,
                )
            )
            continue
        if observation.suspended:
            surviving.append(position)
            decisions.append(
                PositionDecision(
                    code=position.code,
                    status="suspended",
                    reason=position.pending_exit_reason,
                    final_profit_line=position.final_profit_line,
                )
            )
            continue

        if position.pending_exit_reason is not None:
            pending_order = _pending_exit_order(
                source,
                position,
                observation,
            )
            if pending_order is None:
                surviving.append(position)
                decisions.append(
                    PositionDecision(
                        code=position.code,
                        status="pending_exit",
                        reason=position.pending_exit_reason,
                        final_profit_line=position.final_profit_line,
                    )
                )
            else:
                orders.append(pending_order)
                decisions.append(
                    PositionDecision(
                        code=position.code,
                        status="exited",
                        reason=pending_order.reason,
                        execution_session=pending_order.execution_session,
                        final_profit_line=position.final_profit_line,
                    )
                )
            continue

        position.highest_close_since_entry = max(
            position.highest_close_since_entry,
            observation.close,
        )
        technical_complete = observation.technical_signals_complete
        valid_breakout = (
            technical_complete
            and (
                observation.confirmed_pressure_breakout
                or observation.valid_volume_breakout
            )
        )
        strong_continuation = (
            technical_complete and observation.strong_bullish_continuation
        )
        if valid_breakout:
            if observation.new_stop_price is not None:
                position.stop_price = observation.new_stop_price
            # 压力确认突破后必须寻找新压力；
            # 没有新压力即进入新高状态。
            position.pressure_target = observation.new_pressure_target

        _update_profit_protection(
            position,
            observation,
            allow_activation=(
                position.pressure_target is None
                or position.profit_protection_active
            ),
        )
        reason = _new_exit_reason(
            position,
            observation,
            technical_complete=technical_complete,
            valid_breakout=valid_breakout,
            strong_continuation=strong_continuation,
        )
        if reason is None:
            surviving.append(position)
            decisions.append(
                PositionDecision(
                    code=position.code,
                    status="holding",
                    final_profit_line=position.final_profit_line,
                )
            )
            continue

        if _is_limit_down(observation.close, observation.limit_down_price):
            position.pending_exit_reason = reason
            surviving.append(position)
            decisions.append(
                PositionDecision(
                    code=position.code,
                    status="pending_exit",
                    reason=reason,
                    final_profit_line=position.final_profit_line,
                )
            )
            continue

        order = _sell_order(
            source,
            position,
            price=observation.close,
            session="close",
            reason=reason,
        )
        orders.append(order)
        decisions.append(
            PositionDecision(
                code=position.code,
                status="exited",
                reason=reason,
                execution_session="close",
                final_profit_line=position.final_profit_line,
            )
        )

    return PositionManagementDecision(
        actual_date=source.actual_date,
        strategy_version=source.strategy_version,
        publication_blocked=False,
        decisions=decisions,
        positions=surviving,
        orders=orders,
    )


def _pending_exit_order(
    source: PositionManagementInput,
    position: HoldingPosition,
    observation: PositionObservation,
) -> SimulatedSellOrder | None:
    assert position.pending_exit_reason is not None
    if not _is_limit_down(observation.open, observation.limit_down_price):
        return _sell_order(
            source,
            position,
            price=observation.open,
            session="open",
            reason=position.pending_exit_reason,
        )
    if not _is_limit_down(observation.close, observation.limit_down_price):
        return _sell_order(
            source,
            position,
            price=observation.close,
            session="close",
            reason=position.pending_exit_reason,
        )
    return None


def _update_profit_protection(
    position: HoldingPosition,
    observation: PositionObservation,
    *,
    allow_activation: bool,
) -> None:
    activation_floor = position.cost_price + position.initial_risk
    if (
        not position.profit_protection_active
        and allow_activation
        and observation.close >= activation_floor
    ):
        position.profit_protection_active = True
    if not position.profit_protection_active:
        return

    trailing_line = (
        position.cost_price
        + (
            position.highest_close_since_entry
            - position.cost_price
        )
        * 0.5
    )
    candidates = [activation_floor, trailing_line]
    if position.final_profit_line is not None:
        candidates.append(position.final_profit_line)
    if observation.latest_support_line is not None:
        candidates.append(observation.latest_support_line)
    position.final_profit_line = max(candidates)


def _new_exit_reason(
    position: HoldingPosition,
    observation: PositionObservation,
    *,
    technical_complete: bool,
    valid_breakout: bool,
    strong_continuation: bool,
) -> ExitReason | None:
    if observation.close < position.stop_price:
        return "stop_close_break"
    if technical_complete and observation.strong_bearish:
        return "strong_bearish"
    if technical_complete and observation.standard_rsi_top_divergence:
        return "standard_rsi_top_divergence"
    if technical_complete and observation.weak_bearish_rsi_composite:
        return "weak_bearish_rsi_composite"
    if (
        position.profit_protection_active
        and position.final_profit_line is not None
        and observation.close < position.final_profit_line
    ):
        return "final_profit_protection"
    if (
        position.pressure_target is not None
        and observation.high >= position.pressure_target
        and not valid_breakout
        and not strong_continuation
    ):
        return "pressure_target_touched"
    return None


def _sell_order(
    source: PositionManagementInput,
    position: HoldingPosition,
    *,
    price: float,
    session: ExecutionSession,
    reason: ExitReason,
) -> SimulatedSellOrder:
    notional = round(position.shares * price, 2)
    commission = round(
        max(
            source.fees.minimum_commission,
            notional * source.fees.commission_rate,
        ),
        2,
    )
    transfer_fee = round(notional * source.fees.transfer_fee_rate, 2)
    stamp_tax = round(notional * source.fees.stamp_tax_rate, 2)
    return SimulatedSellOrder(
        actual_date=source.actual_date,
        strategy_version=source.strategy_version,
        code=position.code,
        name=position.name,
        execution_session=session,
        execution_price=price,
        shares=position.shares,
        notional=notional,
        commission=commission,
        transfer_fee=transfer_fee,
        stamp_tax=stamp_tax,
        net_cash=round(
            notional - commission - transfer_fee - stamp_tax,
            2,
        ),
        reason=reason,
    )


def _is_limit_down(price: float, limit_down_price: float) -> bool:
    return price <= limit_down_price + 1e-8
