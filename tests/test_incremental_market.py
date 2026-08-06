from __future__ import annotations

from datetime import date

import pandas as pd

from fourseasquant.akshare_history import SecurityListing
from fourseasquant.incremental_market import (
    QfqFactor,
    build_incremental_market_batch,
)


def test_snapshot_batch_builds_exact_raw_and_qfq_target_facts() -> None:
    target_date = date(2026, 7, 30)
    snapshot = pd.DataFrame(
        [
            {
                "代码": "sh600000",
                "名称": "浦发银行",
                "最新价": 10.5,
                "涨跌幅": 1.25,
                "昨收": 10.37,
                "今开": 10.4,
                "最高": 10.6,
                "最低": 10.3,
                "成交量": 1_000_000,
                "成交额": 10_500_000,
            },
            {
                "代码": "sz000001",
                "名称": "平安银行",
                "最新价": 12.0,
                "涨跌幅": -0.5,
                "昨收": 12.06,
                "今开": 12.1,
                "最高": 12.2,
                "最低": 11.9,
                "成交量": 2_000_000,
                "成交额": 24_000_000,
            },
        ]
    )
    listings = [
        SecurityListing(
            code="600000",
            symbol="sh600000",
            name="浦发银行",
            listing_date=date(1999, 11, 10),
        ),
        SecurityListing(
            code="000001",
            symbol="sz000001",
            name="平安银行",
            listing_date=date(1991, 4, 3),
        ),
    ]
    factors = {
        "600000": QfqFactor(date(2026, 6, 1), 2.0),
        "000001": QfqFactor(date(2026, 6, 1), 4.0),
    }

    batch = build_incremental_market_batch(
        snapshot,
        target_date=target_date,
        listings=listings,
        trading_dates=[
            date(2026, 4, 1),
            date(2026, 7, 29),
            target_date,
        ],
        qfq_factors=factors,
        new_stock_exclusion_days=2,
    )

    assert [fact.code for fact in batch.raw_facts] == ["000001", "600000"]
    assert [fact.code for fact in batch.qfq_facts] == ["000001", "600000"]
    raw = {fact.code: fact for fact in batch.raw_facts}
    qfq = {fact.code: fact for fact in batch.qfq_facts}
    assert raw["600000"].close == 10.5
    assert raw["600000"].previous_close == 10.37
    assert qfq["600000"].open == 5.2
    assert qfq["600000"].close == 5.25
    assert qfq["600000"].previous_close == 5.18
    assert qfq["000001"].close == 3.0


def test_snapshot_batch_excludes_stale_new_and_missing_factor_rows() -> None:
    target_date = date(2026, 7, 30)
    snapshot = pd.DataFrame(
        [
            {
                "代码": "600000",
                "名称": "停牌股票",
                "最新价": 10,
                "涨跌幅": 0,
                "昨收": 10,
                "今开": 0,
                "最高": 0,
                "最低": 0,
                "成交量": 0,
                "成交额": 0,
            },
            {
                "代码": "000001",
                "名称": "新上市",
                "最新价": 12,
                "涨跌幅": 1,
                "昨收": 11.88,
                "今开": 11.9,
                "最高": 12.1,
                "最低": 11.8,
                "成交量": 100,
                "成交额": 1_200,
            },
        ]
    )
    listings = [
        SecurityListing(
            code="600000",
            symbol="sh600000",
            name="停牌股票",
            listing_date=date(2000, 1, 1),
        ),
        SecurityListing(
            code="000001",
            symbol="sz000001",
            name="新上市",
            listing_date=target_date,
        ),
    ]

    batch = build_incremental_market_batch(
        snapshot,
        target_date=target_date,
        listings=listings,
        trading_dates=[date(2026, 7, 29), target_date],
        qfq_factors={"600000": QfqFactor(date(2026, 1, 1), 1.0)},
        new_stock_exclusion_days=2,
    )

    assert batch.raw_facts == []
    assert batch.qfq_facts == []


def test_latest_qfq_factor_one_keeps_target_prices_equal_to_raw() -> None:
    target_date = date(2026, 7, 30)
    snapshot = pd.DataFrame(
        [
            {
                "代码": "600000",
                "名称": "浦发银行",
                "最新价": 10.51,
                "涨跌幅": 1.35,
                "昨收": 10.37,
                "今开": 10.4,
                "最高": 10.6,
                "最低": 10.3,
                "成交量": 1_000,
                "成交额": 10_500,
            }
        ]
    )
    listing = SecurityListing(
        code="600000",
        symbol="sh600000",
        name="浦发银行",
        listing_date=date(1999, 11, 10),
    )

    batch = build_incremental_market_batch(
        snapshot,
        target_date=target_date,
        listings=[listing],
        trading_dates=[date(2026, 7, 29), target_date],
        qfq_factors={
            "600000": QfqFactor(
                effective_date=target_date,
                value=1.0,
            )
        },
        new_stock_exclusion_days=2,
    )

    assert batch.qfq_facts[0].open == batch.raw_facts[0].open
    assert batch.qfq_facts[0].close == batch.raw_facts[0].close
    assert (
        batch.qfq_facts[0].previous_close
        == batch.raw_facts[0].previous_close
    )
