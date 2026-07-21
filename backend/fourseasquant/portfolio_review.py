from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SecurityRef(BaseModel):
    code: str
    name: str


class Holding(BaseModel):
    security: SecurityRef
    weight_pct: float = Field(gt=0, le=100)
    holding_return_pct: float
    industry: str


class Trade(BaseModel):
    date: date
    security: SecurityRef
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    weight_change_pct: float
    execution_price: float = Field(gt=0)


class ReturnContribution(BaseModel):
    security: SecurityRef
    contribution_pct: float


class PortfolioReview(BaseModel):
    date: date
    is_demo: bool
    daily_strategy_return_pct: float
    holdings: list[Holding]
    trades: list[Trade]
    contributions: list[ReturnContribution]

    @model_validator(mode="after")
    def validate_portfolio_consistency(self) -> PortfolioReview:
        if not 20 <= len(self.holdings) <= 30:
            raise ValueError("持仓数量必须在 20 至 30 只之间")
        holding_codes = [holding.security.code for holding in self.holdings]
        if len(set(holding_codes)) != len(holding_codes):
            raise ValueError("持仓代码必须唯一")
        if abs(sum(holding.weight_pct for holding in self.holdings) - 100) > 0.001:
            raise ValueError("持仓权重合计必须为 100%")
        if any(trade.date != self.date for trade in self.trades):
            raise ValueError("交易日期必须与快照日期一致")
        contribution_codes = [item.security.code for item in self.contributions]
        if set(contribution_codes) != set(holding_codes):
            raise ValueError("收益贡献必须覆盖全部持仓")
        if abs(
            sum(item.contribution_pct for item in self.contributions)
            - self.daily_strategy_return_pct
        ) > 0.0001:
            raise ValueError("收益贡献汇总必须等于当日策略收益")
        return self
