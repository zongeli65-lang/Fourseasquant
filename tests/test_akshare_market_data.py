from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from fourseasquant.akshare_market_data import (
    AkshareMarketDataAdapter,
    BenchmarkDailyFact,
    DailyMarketFacts,
    MarketDataQualityError,
    MarketDataSourceError,
    SecurityDailyFact,
    build_akshare_market_data_adapter,
    collect_and_store_daily_facts,
)
from fourseasquant.database import initialize_database, latest_market_facts


def test_load_daily_facts_returns_only_eligible_main_board_securities() -> None:
    trading_dates = pd.bdate_range("2026-06-01", "2026-07-21")
    index_history = pd.DataFrame(
        {
            "date": trading_dates.date,
            "open": range(4500, 4500 + len(trading_dates)),
            "high": range(4510, 4510 + len(trading_dates)),
            "low": range(4490, 4490 + len(trading_dates)),
            "close": range(4505, 4505 + len(trading_dates)),
            "volume": [30_000_000_000] * len(trading_dates),
        }
    )
    sh_listing = pd.DataFrame(
        [
            {"证券代码": "600000", "证券简称": "浦发银行", "上市日期": date(1999, 11, 10)},
            {"证券代码": "600001", "证券简称": "ST示例", "上市日期": date(2000, 1, 1)},
        ]
    )
    sz_listing = pd.DataFrame(
        [
            {"板块": "主板", "A股代码": "000001", "A股简称": "平安银行", "A股上市日期": "1991-04-03"},
            {"板块": "主板", "A股代码": "001999", "A股简称": "今日停牌", "A股上市日期": "2010-01-01"},
            {"板块": "主板", "A股代码": "002999", "A股简称": "新上市", "A股上市日期": "2026-07-10"},
            {"板块": "创业板", "A股代码": "300001", "A股简称": "创业样本", "A股上市日期": "2010-01-01"},
        ]
    )
    snapshot = pd.DataFrame(
        [
            {"代码": "sh600000", "名称": "浦发银行", "最新价": 10.5, "涨跌幅": 1.25, "昨收": 10.37, "今开": 10.4, "最高": 10.6, "最低": 10.3, "成交量": 1_000_000.0, "成交额": 10_500_000.0},
            {"代码": "sh600001", "名称": "ST示例", "最新价": 5.0, "涨跌幅": 2.0, "昨收": 4.9, "今开": 4.9, "最高": 5.0, "最低": 4.8, "成交量": 500_000.0, "成交额": 2_500_000.0},
            {"代码": "sz000001", "名称": "平安银行", "最新价": 12.0, "涨跌幅": -0.5, "昨收": 12.06, "今开": 12.1, "最高": 12.2, "最低": 11.9, "成交量": 2_000_000.0, "成交额": 24_000_000.0},
            {"代码": "sz001999", "名称": "今日停牌", "最新价": 8.0, "涨跌幅": 0.0, "昨收": 8.0, "今开": 0.0, "最高": 0.0, "最低": 0.0, "成交量": 0.0, "成交额": 0.0},
            {"代码": "sz002999", "名称": "新上市", "最新价": 20.0, "涨跌幅": 10.0, "昨收": 18.18, "今开": 19.0, "最高": 20.0, "最低": 18.8, "成交量": 300_000.0, "成交额": 6_000_000.0},
            {"代码": "sz300001", "名称": "创业样本", "最新价": 15.0, "涨跌幅": 3.0, "昨收": 14.56, "今开": 14.7, "最高": 15.1, "最低": 14.5, "成交量": 600_000.0, "成交额": 9_000_000.0},
        ]
    )

    adapter = AkshareMarketDataAdapter(
        stock_snapshot=lambda: snapshot,
        sh_listing=lambda: sh_listing,
        sz_listing=lambda: sz_listing,
        index_history=lambda: index_history,
    )

    facts = adapter.load_daily_facts(
        date(2026, 7, 22),
        new_stock_exclusion_days=20,
    )

    assert facts.source == "akshare"
    assert facts.actual_data_date == date(2026, 7, 21)
    assert facts.benchmark.name == "沪深 300"
    assert facts.benchmark.close == 4541.0
    assert [security.code for security in facts.securities] == ["000001", "600000"]
    assert facts.securities[0].change_pct == -0.5
    assert facts.securities[1].turnover_cny == 10_500_000


def test_load_daily_facts_rejects_an_empty_eligible_universe() -> None:
    adapter = AkshareMarketDataAdapter(
        stock_snapshot=lambda: pd.DataFrame(
            [
                {
                    "代码": "sh600000",
                    "名称": "浦发银行",
                    "最新价": 10.5,
                    "涨跌幅": 0.0,
                    "昨收": 10.5,
                    "今开": 0.0,
                    "最高": 0.0,
                    "最低": 0.0,
                    "成交量": 0.0,
                    "成交额": 0.0,
                }
            ]
        ),
        sh_listing=lambda: pd.DataFrame(
            [
                {
                    "证券代码": "600000",
                    "证券简称": "浦发银行",
                    "上市日期": date(1999, 11, 10),
                }
            ]
        ),
        sz_listing=lambda: pd.DataFrame(
            columns=["板块", "A股代码", "A股简称", "A股上市日期"]
        ),
        index_history=lambda: pd.DataFrame(
            [
                {
                    "date": date(2026, 7, 21),
                    "open": 4500,
                    "high": 4550,
                    "low": 4490,
                    "close": 4540,
                    "volume": 30_000_000_000,
                }
            ]
        ),
    )

    with pytest.raises(MarketDataQualityError, match="没有合格的沪深主板个股"):
        adapter.load_daily_facts(
            date(2026, 7, 22),
            new_stock_exclusion_days=20,
        )


def test_latest_snapshot_is_never_relabelled_as_historical_data() -> None:
    adapter = AkshareMarketDataAdapter(
        stock_snapshot=pd.DataFrame,
        sh_listing=pd.DataFrame,
        sz_listing=pd.DataFrame,
        index_history=lambda: pd.DataFrame(
            [
                {
                    "date": date(2026, 7, 18),
                    "open": 4500,
                    "high": 4550,
                    "low": 4490,
                    "close": 4540,
                    "volume": 30_000_000_000,
                },
                {
                    "date": date(2026, 7, 21),
                    "open": 4540,
                    "high": 4760,
                    "low": 4530,
                    "close": 4739,
                    "volume": 31_000_000_000,
                },
            ]
        ),
    )

    with pytest.raises(
        MarketDataQualityError, match="最新快照不能用于历史日期"
    ):
        adapter.load_daily_facts(
            date(2026, 7, 18),
            new_stock_exclusion_days=20,
        )


def test_load_daily_facts_normalises_upstream_network_errors() -> None:
    def broken_index_history() -> pd.DataFrame:
        raise ConnectionError("上游主动断开并包含内部地址")

    adapter = AkshareMarketDataAdapter(
        stock_snapshot=pd.DataFrame,
        sh_listing=pd.DataFrame,
        sz_listing=pd.DataFrame,
        index_history=broken_index_history,
    )

    with pytest.raises(MarketDataSourceError, match="AKShare 真实数据读取失败") as raised:
        adapter.load_daily_facts(
            date(2026, 7, 22),
            new_stock_exclusion_days=20,
        )

    assert isinstance(raised.value.__cause__, ConnectionError)
    assert "内部地址" not in str(raised.value)


def test_production_adapter_uses_non_eastmoney_akshare_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "akshare.stock_zh_a_spot",
        lambda: pd.DataFrame(
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
                    "成交量": 1_000_000.0,
                    "成交额": 10_500_000.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "akshare.stock_info_sh_name_code",
        lambda *, symbol: pd.DataFrame(
            [
                {
                    "证券代码": "600000",
                    "证券简称": "浦发银行",
                    "上市日期": date(1999, 11, 10),
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "akshare.stock_info_sz_name_code",
        lambda *, symbol: pd.DataFrame(
            columns=["板块", "A股代码", "A股简称", "A股上市日期"]
        ),
    )
    monkeypatch.setattr(
        "akshare.stock_zh_index_daily",
        lambda *, symbol: pd.DataFrame(
            {
                "date": pd.bdate_range("2026-06-01", "2026-07-21").date,
                "open": 4500,
                "high": 4550,
                "low": 4490,
                "close": 4540,
                "volume": 30_000_000_000,
            }
        ),
    )

    facts = build_akshare_market_data_adapter().load_daily_facts(
        date(2026, 7, 22),
        new_stock_exclusion_days=20,
    )

    assert facts.actual_data_date == date(2026, 7, 21)
    assert [security.code for security in facts.securities] == ["600000"]


def test_collect_and_store_daily_facts_persists_only_validated_batch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "facts.db"
    initialize_database(database)
    collected_at = datetime(
        2026, 7, 21, 16, 31, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    facts = DailyMarketFacts(
        source="akshare",
        requested_date=date(2026, 7, 22),
        actual_data_date=date(2026, 7, 21),
        benchmark=BenchmarkDailyFact(
            name="沪深 300",
            date=date(2026, 7, 21),
            open=4700,
            high=4750,
            low=4680,
            close=4739.229,
            volume=30_000_000_000,
        ),
        securities=[
            SecurityDailyFact(
                code="000001",
                name="平安银行",
                date=date(2026, 7, 21),
                open=12.1,
                high=12.2,
                low=11.9,
                close=12.0,
                previous_close=12.06,
                change_pct=-0.5,
                volume=2_000_000,
                turnover_cny=24_000_000,
                listing_trading_days=8_000,
            )
        ],
    )

    stored = collect_and_store_daily_facts(
        date(2026, 7, 22),
        path=database,
        new_stock_exclusion_days=20,
        adapter=_FixedAdapter(facts),
        collected_at=collected_at,
    )

    assert stored == facts
    row = latest_market_facts(database, date(2026, 7, 21))
    assert row is not None
    assert row.source == "akshare"
    assert row.collected_at == collected_at
    assert DailyMarketFacts.model_validate_json(row.payload_json) == facts


def test_failed_collection_does_not_replace_last_successful_facts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "facts.db"
    initialize_database(database)
    successful_date = date(2026, 7, 21)
    successful_payload = '{"status":"complete"}'
    from fourseasquant.database import save_market_facts

    save_market_facts(
        database,
        actual_data_date=successful_date,
        source="akshare",
        payload_json=successful_payload,
        collected_at=datetime(
            2026, 7, 21, 16, 31, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )

    with pytest.raises(MarketDataSourceError):
        collect_and_store_daily_facts(
            date(2026, 7, 22),
            path=database,
            new_stock_exclusion_days=20,
            adapter=_BrokenAdapter(),
        )

    row = latest_market_facts(database, successful_date)
    assert row is not None
    assert row.payload_json == successful_payload


class _FixedAdapter:
    def __init__(self, facts: DailyMarketFacts) -> None:
        self._facts = facts

    def load_daily_facts(
        self, requested_date: date, *, new_stock_exclusion_days: int
    ) -> DailyMarketFacts:
        return self._facts


class _BrokenAdapter:
    def load_daily_facts(
        self, requested_date: date, *, new_stock_exclusion_days: int
    ) -> DailyMarketFacts:
        raise MarketDataSourceError("AKShare 真实数据读取失败")
