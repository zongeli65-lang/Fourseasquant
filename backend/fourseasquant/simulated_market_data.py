from __future__ import annotations

from fourseasquant.market_overview import (
    IndexMove,
    LimitStatus,
    MarketOverview,
    SecurityObservation,
    TradingBoard,
    calculate_market_overview,
)
from fourseasquant.sector_performance import (
    SectorMove,
    SectorPerformance,
    build_sector_ranking,
)


SECTOR_CHANGES = [
    4.8,
    4.1,
    3.6,
    3.1,
    2.8,
    2.5,
    2.2,
    1.9,
    1.7,
    1.5,
    0.8,
    0.2,
    -0.1,
    -0.4,
    -0.7,
    -1.0,
    -1.3,
    -1.6,
    -1.9,
    -2.2,
    -2.6,
    -3.0,
    -3.5,
    -4.2,
]


def simulated_market_overview(
    new_stock_exclusion_days: int = 20,
    *,
    turnover_scale: float = 1.0,
) -> MarketOverview:
    observations = [
        SecurityObservation(
            code="600001",
            board=TradingBoard.SHANGHAI_MAIN,
            change_pct=2.5,
            turnover_cny=100_000_000_000,
            average_turnover_20d_cny=90_000_000_000,
            is_new_high_20d=True,
        ),
        SecurityObservation(
            code="000001",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=-1.0,
            turnover_cny=80_000_000_000,
            average_turnover_20d_cny=70_000_000_000,
            is_new_low_20d=True,
        ),
        SecurityObservation(
            code="002001",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=0.0,
            turnover_cny=20_000_000_000,
            average_turnover_20d_cny=20_000_000_000,
        ),
        SecurityObservation(
            code="600002",
            board=TradingBoard.SHANGHAI_MAIN,
            change_pct=10.0,
            limit_status=LimitStatus.LIMIT_UP,
            limit_up_streak=3,
            turnover_cny=40_000_000_000,
            average_turnover_20d_cny=30_000_000_000,
            is_new_high_20d=True,
        ),
        SecurityObservation(
            code="000002",
            board=TradingBoard.SHENZHEN_MAIN,
            change_pct=-10.0,
            limit_status=LimitStatus.LIMIT_DOWN,
            turnover_cny=30_000_000_000,
            average_turnover_20d_cny=30_000_000_000,
            is_new_low_20d=True,
        ),
        SecurityObservation(
            code="688001",
            board=TradingBoard.STAR,
            change_pct=20.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="300001",
            board=TradingBoard.CHINEXT,
            change_pct=-20.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="600003",
            board=TradingBoard.SHANGHAI_MAIN,
            is_st=True,
            change_pct=5.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="000003",
            board=TradingBoard.SHENZHEN_MAIN,
            is_suspended=True,
            change_pct=0.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="600004",
            board=TradingBoard.SHANGHAI_MAIN,
            is_delisting=True,
            change_pct=-10.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
        SecurityObservation(
            code="000004",
            board=TradingBoard.SHENZHEN_MAIN,
            listing_trading_days=19,
            change_pct=10.0,
            turnover_cny=900_000_000_000,
            average_turnover_20d_cny=100_000_000_000,
        ),
    ]
    scaled_observations = [
        observation.model_copy(
            update={
                "turnover_cny": observation.turnover_cny * turnover_scale,
                "average_turnover_20d_cny": (
                    observation.average_turnover_20d_cny * turnover_scale
                ),
            }
        )
        for observation in observations
    ]
    return calculate_market_overview(
        scaled_observations,
        [
            IndexMove(name="上证指数", change_pct=0.62),
            IndexMove(name="深证成指", change_pct=-0.31),
            IndexMove(name="创业板指", change_pct=0.18),
            IndexMove(name="沪深 300", change_pct=0.44),
        ],
        new_stock_exclusion_days=new_stock_exclusion_days,
    )


def simulated_sector_performance() -> SectorPerformance:
    industry_names = [
        "有色金属",
        "国防军工",
        "电子",
        "通信",
        "机械设备",
        "汽车",
        "基础化工",
        "电力设备",
        "计算机",
        "医药生物",
        "传媒",
        "轻工制造",
        "社会服务",
        "建筑材料",
        "食品饮料",
        "交通运输",
        "公用事业",
        "银行",
        "非银金融",
        "房地产",
        "商贸零售",
        "农林牧渔",
        "煤炭",
        "钢铁",
    ]
    concept_names = [
        "人形机器人",
        "低空经济",
        "先进封装",
        "卫星互联网",
        "液冷服务器",
        "工业母机",
        "存储芯片",
        "智能驾驶",
        "算力租赁",
        "创新药",
        "数据要素",
        "消费电子",
        "国企改革",
        "新型城镇化",
        "预制菜",
        "航运港口",
        "绿色电力",
        "中特估",
        "券商",
        "租售同权",
        "免税店",
        "种业",
        "煤化工",
        "钢铁互联网",
    ]
    industries = [
        SectorMove(name=name, change_pct=change)
        for name, change in zip(industry_names, SECTOR_CHANGES, strict=True)
    ]
    concepts = [
        SectorMove(name=name, change_pct=round(change * 1.08, 2))
        for name, change in zip(concept_names, SECTOR_CHANGES, strict=True)
    ]
    return SectorPerformance(
        industries=build_sector_ranking(industries),
        concepts=build_sector_ranking(concepts),
    )
