from __future__ import annotations

import math
from datetime import date

from fourseasquant.portfolio_review import (
    Holding,
    PortfolioReview,
    ReturnContribution,
    SecurityRef,
    Trade,
)


def simulated_portfolio_review(
    target_date: date,
    daily_strategy_return_pct: float,
) -> PortfolioReview:
    industries = [
        "电子",
        "机械设备",
        "有色金属",
        "汽车",
        "医药生物",
        "通信",
        "基础化工",
        "电力设备",
    ]
    raw_weights = list(range(24, 0, -1))
    weight_total = sum(raw_weights)
    weights = [round(weight / weight_total * 100, 4) for weight in raw_weights]
    weights[-1] = round(100 - sum(weights[:-1]), 4)
    holdings = [
        Holding(
            security=SecurityRef(
                code=f"{600101 + index:06d}",
                name=f"演示股票 {index + 1:02d}",
            ),
            weight_pct=weights[index],
            holding_return_pct=round(math.sin(index * 0.73) * 12.5, 2),
            industry=industries[index % len(industries)],
        )
        for index in range(24)
    ]
    trades = [
        Trade(
            date=target_date,
            security=holding.security,
            side="buy" if index % 2 == 0 else "sell",
            quantity=1000 + index * 300,
            weight_change_pct=round(
                (1 if index % 2 == 0 else -1) * (0.2 + index * 0.08),
                2,
            ),
            execution_price=round(12.5 + index * 3.17, 2),
        )
        for index, holding in enumerate(holdings[:6])
    ]
    contribution_values = [
        round(math.sin(index * 0.67) * 0.035, 6) for index in range(23)
    ]
    contribution_values.append(
        round(daily_strategy_return_pct - sum(contribution_values), 6)
    )
    contributions = [
        ReturnContribution(
            security=holding.security,
            contribution_pct=contribution_values[index],
        )
        for index, holding in enumerate(holdings)
    ]
    return PortfolioReview(
        date=target_date,
        is_demo=True,
        daily_strategy_return_pct=daily_strategy_return_pct,
        holdings=holdings,
        trades=trades,
        contributions=contributions,
    )
