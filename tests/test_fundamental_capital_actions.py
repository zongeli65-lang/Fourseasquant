from datetime import date, datetime
from pathlib import Path

import pandas as pd

from fourseasquant.database import initialize_database
from fourseasquant.fundamental_capital_actions import (
    CapitalActionEvent,
    CapitalActionEventType,
    CapitalActionSnapshot,
    aggregate_shareholder_actions,
    calculate_average_floating_market_cap,
    collect_capital_action_snapshot,
    normalize_cancelled_buybacks,
    normalize_management_change_frames,
    normalize_share_change_frame,
)
from fourseasquant.fundamental_repository import (
    read_latest_published_capital_action_snapshot,
    save_capital_action_snapshot,
)


def test_management_changes_only_confirm_active_cash_trades() -> None:
    increases = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "截止日期": "2026-07-20",
                "公告日期": "2026-07-21",
                "高管姓名": "甲",
                "变动数量": 10_000,
                "成交均价": 12.5,
                "持股变动原因": "竞价交易",
                "数据来源": "临时公告",
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "截止日期": "2026-07-22",
                "公告日期": "2026-07-23",
                "高管姓名": "乙",
                "变动数量": 5_000,
                "成交均价": 10,
                "持股变动原因": "股权激励",
                "数据来源": "临时公告",
            },
        ]
    )
    decreases = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "截止日期": "2026-07-24",
                "公告日期": "2026-07-24",
                "高管姓名": "丙",
                "变动数量": -2_000,
                "成交均价": 15,
                "持股变动原因": "竞价交易",
                "数据来源": "临时公告",
            }
        ]
    )

    events = normalize_management_change_frames(
        increases=increases,
        decreases=decreases,
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
    )

    assert [event.event_type for event in events] == [
        CapitalActionEventType.insider_buy,
        CapitalActionEventType.insider_buy,
        CapitalActionEventType.insider_sell,
    ]
    assert events[0].confirmed_for_score is True
    assert events[0].amount_cny == 125_000
    assert events[0].announcement_at.date() == date(2026, 7, 21)
    assert events[1].confirmed_for_score is False
    assert events[1].exclusion_reason == "非主动现金交易：股权激励"
    assert events[2].confirmed_for_score is True
    assert events[2].amount_cny == 30_000


def test_incomplete_management_window_never_activates_insider_score() -> None:
    frame = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "截止日期": "2025-07-23",
                "公告日期": "2025-07-23",
                "高管姓名": "旧记录",
                "变动数量": 99_000,
                "成交均价": 12.5,
                "持股变动原因": "竞价交易",
                "数据来源": "临时公告",
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "截止日期": "2026-07-20",
                "公告日期": "2026-07-21",
                "高管姓名": "甲",
                "变动数量": 10_000,
                "成交均价": 12.5,
                "持股变动原因": "竞价交易",
                "数据来源": "临时公告",
            }
        ]
    )

    snapshot = collect_capital_action_snapshot(
        codes=["600000"],
        as_of_date=date(2026, 7, 24),
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
        management_increases=frame,
        management_decreases=frame.iloc[0:0],
        repurchases=pd.DataFrame(
            columns=[
                "股票代码",
                "股票简称",
                "回购起始时间",
                "实施进度",
                "已回购股份数量",
                "已回购金额",
                "最新公告日期",
            ]
        ),
        share_change_fetcher=lambda _code: pd.DataFrame(
            columns=[
                "证券代码",
                "证券简称",
                "公告日期",
                "变动日期",
                "变动原因",
                "总股本",
                "已流通股份",
            ]
        ),
        announcement_fetcher=lambda _code: pd.DataFrame(),
        management_coverage_complete=False,
    )

    assert snapshot.complete is True
    assert len(snapshot.events) == 1
    assert snapshot.events[0].confirmed_for_score is False
    assert snapshot.events[0].exclusion_reason == (
        "高管增减持接口无法证明目标日前十二个月窗口完整"
    )
    actions = aggregate_shareholder_actions(
        events=snapshot.events,
        as_of_date=date(2026, 7, 24),
        average_floating_market_cap=1_000_000_000,
        insider_window_complete=snapshot.insider_window_complete,
    )
    assert actions.insider_window_complete is False
    assert actions.insider_net_purchase_amount is None


def test_window_keeps_dilution_announced_earlier_but_effective_recently() -> None:
    empty_management = pd.DataFrame(
        columns=[
            "证券代码",
            "证券简称",
            "截止日期",
            "公告日期",
            "变动数量",
            "成交均价",
            "持股变动原因",
        ]
    )
    share_changes = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2025-01-01",
                "变动日期": "2025-01-01",
                "变动原因": "期初股本",
                "总股本": 10_000,
                "已流通股份": 9_000,
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2025-06-01",
                "变动日期": "2025-08-01",
                "变动原因": "非公开增发",
                "总股本": 11_000,
                "已流通股份": 9_000,
            },
        ]
    )

    snapshot = collect_capital_action_snapshot(
        codes=["600000"],
        as_of_date=date(2026, 7, 24),
        collected_at=datetime.fromisoformat("2026-07-24T16:30:00+08:00"),
        management_increases=empty_management,
        management_decreases=empty_management,
        repurchases=pd.DataFrame(
            columns=[
                "股票代码",
                "股票简称",
                "回购起始时间",
                "实施进度",
                "已回购股份数量",
                "已回购金额",
                "最新公告日期",
            ]
        ),
        share_change_fetcher=lambda _code: share_changes,
        announcement_fetcher=lambda _code: pd.DataFrame(),
    )

    assert len(snapshot.events) == 1
    assert snapshot.events[0].event_type == (
        CapitalActionEventType.share_dilution
    )


def test_share_changes_only_emit_external_financing_dilution() -> None:
    frame = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2025-12-31",
                "变动日期": "2025-12-31",
                "变动原因": "期初股本",
                "总股本": 10_000,
                "已流通股份": 9_000,
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2026-03-01",
                "变动日期": "2026-03-01",
                "变动原因": "非公开增发",
                "总股本": 11_000,
                "已流通股份": 9_000,
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2026-05-01",
                "变动日期": "2026-05-01",
                "变动原因": "资本公积转增",
                "总股本": 12_000,
                "已流通股份": 10_000,
            },
        ]
    )

    events = normalize_share_change_frame(
        frame=frame,
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
    )

    assert len(events) == 1
    assert events[0].event_type == CapitalActionEventType.share_dilution
    assert events[0].confirmed_for_score is True
    assert events[0].shares == 10_000_000
    assert events[0].shares_before == 100_000_000
    assert events[0].reason == "非公开增发"


def test_share_change_without_announcement_date_is_not_scored() -> None:
    frame = pd.DataFrame(
        [
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": "2025-12-31",
                "变动日期": "2025-12-31",
                "变动原因": "期初股本",
                "总股本": 10_000,
                "已流通股份": 9_000,
            },
            {
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "公告日期": None,
                "变动日期": "2026-03-01",
                "变动原因": "非公开增发",
                "总股本": 11_000,
                "已流通股份": 9_000,
            },
        ]
    )

    events = normalize_share_change_frame(
        frame=frame,
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
    )

    assert events == []


def test_buyback_only_scores_after_official_cancellation_confirmation() -> None:
    repurchases = pd.DataFrame(
        [
            {
                "股票代码": "600000",
                "股票简称": "浦发银行",
                "回购起始时间": "2026-01-02",
                "实施进度": "完成实施",
                "已回购股份数量": 2_000_000,
                "已回购金额": 20_000_000,
                "最新公告日期": "2026-06-01",
            },
            {
                "股票代码": "000001",
                "股票简称": "平安银行",
                "回购起始时间": "2026-01-02",
                "实施进度": "完成实施",
                "已回购股份数量": 1_000_000,
                "已回购金额": 8_000_000,
                "最新公告日期": "2026-06-01",
            },
        ]
    )
    announcements = pd.DataFrame(
        [
            {
                "代码": "600000",
                "简称": "浦发银行",
                "公告标题": "关于完成<em>回购</em>股份注销暨股份变动的公告",
                "公告时间": "2026-06-15",
                "公告链接": "https://www.cninfo.com.cn/600000-buyback",
                "注销金额": 20_000_000,
                "注销股份数量": 2_000_000,
                "官方文件哈希": "a" * 64,
            },
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "关于回购股份实施完成的公告",
                "公告时间": "2026-06-15",
                "公告链接": "https://www.cninfo.com.cn/000001-buyback",
                "注销金额": None,
                "注销股份数量": None,
            },
        ]
    )

    events = normalize_cancelled_buybacks(
        repurchases=repurchases,
        announcements=announcements,
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
    )

    assert len(events) == 2
    confirmed = next(event for event in events if event.code == "600000")
    pending = next(event for event in events if event.code == "000001")
    assert confirmed.event_type == CapitalActionEventType.cancelled_buyback
    assert confirmed.confirmed_for_score is True
    assert confirmed.amount_cny == 20_000_000
    assert confirmed.content_sha256 == "a" * 64
    assert confirmed.source_url == "https://www.cninfo.com.cn/600000-buyback"
    assert pending.event_type == CapitalActionEventType.buyback_announcement
    assert pending.confirmed_for_score is False
    assert pending.exclusion_reason == "缺少官方回购股份注销完成公告"


def test_one_official_cancellation_cannot_confirm_multiple_buyback_rounds() -> None:
    repurchases = pd.DataFrame(
        [
            {
                "股票代码": "600000",
                "股票简称": "浦发银行",
                "回购起始时间": "2025-01-02",
                "实施进度": "完成实施",
                "已回购股份数量": 2_000_000,
                "已回购金额": 20_000_000,
                "最新公告日期": "2026-05-01",
            },
            {
                "股票代码": "600000",
                "股票简称": "浦发银行",
                "回购起始时间": "2026-01-02",
                "实施进度": "完成实施",
                "已回购股份数量": 1_000_000,
                "已回购金额": 10_000_000,
                "最新公告日期": "2026-06-01",
            },
        ]
    )
    announcements = pd.DataFrame(
        [
            {
                "代码": "600000",
                "简称": "浦发银行",
                "公告标题": "关于完成回购股份注销暨股份变动的公告",
                "公告时间": "2026-06-15",
                "公告链接": "https://www.cninfo.com.cn/one-cancellation",
                "注销金额": 20_000_000,
                "注销股份数量": 2_000_000,
            }
        ]
    )

    events = normalize_cancelled_buybacks(
        repurchases=repurchases,
        announcements=announcements,
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
    )

    assert sum(event.confirmed_for_score for event in events) == 1
    assert sum(
        event.event_type == CapitalActionEventType.buyback_announcement
        for event in events
    ) == 1


def test_capital_events_aggregate_over_last_twelve_months() -> None:
    collected_at = datetime.fromisoformat("2026-07-25T16:30:00+08:00")
    events = [
        _event(
            event_type=CapitalActionEventType.insider_buy,
            effective_date=date(2026, 1, 5),
            amount_cny=1_000_000,
            collected_at=collected_at,
        ),
        _event(
            event_type=CapitalActionEventType.insider_sell,
            effective_date=date(2026, 2, 5),
            amount_cny=250_000,
            collected_at=collected_at,
        ),
        _event(
            event_type=CapitalActionEventType.cancelled_buyback,
            effective_date=date(2026, 3, 5),
            amount_cny=2_000_000,
            collected_at=collected_at,
        ),
        _event(
            event_type=CapitalActionEventType.share_dilution,
            effective_date=date(2026, 4, 5),
            shares=10_000_000,
            shares_before=100_000_000,
            collected_at=collected_at,
        ),
        _event(
            event_type=CapitalActionEventType.insider_buy,
            effective_date=date(2025, 1, 1),
            amount_cny=9_000_000,
            collected_at=collected_at,
        ),
    ]

    actions = aggregate_shareholder_actions(
        events=events,
        as_of_date=date(2026, 7, 25),
        average_floating_market_cap=1_000_000_000,
    )

    assert actions.insider_net_purchase_amount == 750_000
    assert actions.cancelled_buyback_amount == 2_000_000
    assert actions.newly_issued_shares == 10_000_000
    assert actions.shares_before_issuance == 100_000_000


def test_incomplete_capital_batch_does_not_replace_last_publication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "capital-actions.db"
    initialize_database(database)
    collected_at = datetime.fromisoformat("2026-07-25T16:30:00+08:00")
    first_event = _event(
        event_type=CapitalActionEventType.insider_buy,
        effective_date=date(2026, 7, 20),
        amount_cny=1_000_000,
        collected_at=collected_at,
    )
    complete = CapitalActionSnapshot(
        as_of_date=date(2026, 7, 25),
        expected_codes=["600000"],
        completed_codes=["600000"],
        events=[first_event],
        errors={},
    )

    first_result = save_capital_action_snapshot(
        database,
        complete,
        collected_at=collected_at,
    )
    incomplete = CapitalActionSnapshot(
        as_of_date=date(2026, 7, 25),
        expected_codes=["000001", "600000"],
        completed_codes=["600000"],
        events=[],
        errors={"000001": "巨潮请求失败"},
    )
    failed_result = save_capital_action_snapshot(
        database,
        incomplete,
        collected_at=collected_at,
    )
    published = read_latest_published_capital_action_snapshot(
        database,
        as_of_date=date(2026, 7, 25),
    )

    assert first_result.published is True
    assert failed_result.published is False
    assert failed_result.status == "failed"
    assert published is not None
    assert published.events == [first_event]
    assert published.expected_codes == ["600000"]


def test_collector_reports_per_stock_failure_without_partial_publication() -> None:
    empty_management = pd.DataFrame(
        columns=[
            "证券代码",
            "证券简称",
            "截止日期",
            "公告日期",
            "变动数量",
            "成交均价",
            "持股变动原因",
        ]
    )
    empty_repurchases = pd.DataFrame(
        columns=[
            "股票代码",
            "股票简称",
            "回购起始时间",
            "实施进度",
            "已回购股份数量",
            "已回购金额",
            "最新公告日期",
        ]
    )

    def fetch_share_changes(code: str) -> pd.DataFrame:
        if code == "000001":
            raise ConnectionError("巨潮连接失败")
        return pd.DataFrame(
            [
                {
                    "证券代码": code,
                    "证券简称": "浦发银行",
                    "公告日期": "2026-01-01",
                    "变动日期": "2026-01-01",
                    "变动原因": "期初股本",
                    "总股本": 10_000,
                    "已流通股份": 9_000,
                }
            ]
        )

    snapshot = collect_capital_action_snapshot(
        codes=["600000", "000001"],
        as_of_date=date(2026, 7, 25),
        collected_at=datetime.fromisoformat("2026-07-25T16:30:00+08:00"),
        management_increases=empty_management,
        management_decreases=empty_management,
        repurchases=empty_repurchases,
        share_change_fetcher=fetch_share_changes,
        announcement_fetcher=lambda _code: pd.DataFrame(
            columns=["代码", "简称", "公告标题", "公告时间", "公告链接"]
        ),
    )

    assert snapshot.complete is False
    assert snapshot.completed_codes == ["600000"]
    assert snapshot.errors == {"000001": "ConnectionError: 巨潮连接失败"}


def test_average_floating_market_cap_uses_effective_share_history() -> None:
    closes = pd.DataFrame(
        [
            {"交易日期": "2026-01-02", "收盘价": 10},
            {"交易日期": "2026-07-01", "收盘价": 20},
        ]
    )
    share_changes = pd.DataFrame(
        [
            {"变动日期": "2025-12-31", "已流通股份": 100},
            {"变动日期": "2026-06-30", "已流通股份": 200},
        ]
    )

    result = calculate_average_floating_market_cap(
        closes=closes,
        share_changes=share_changes,
        as_of_date=date(2026, 7, 25),
    )

    assert result == 25_000_000


def test_average_floating_market_cap_normalizes_mixed_datetime_units() -> None:
    closes = pd.DataFrame(
        {
            "交易日期": pd.Series(
                ["2026-01-02", "2026-07-01"],
                dtype="datetime64[us]",
            ),
            "收盘价": [10, 20],
        }
    )
    share_changes = pd.DataFrame(
        {
            "变动日期": pd.Series(
                ["2025-12-31", "2026-06-30"],
                dtype="datetime64[s]",
            ),
            "已流通股份": [100, 200],
        }
    )

    result = calculate_average_floating_market_cap(
        closes=closes,
        share_changes=share_changes,
        as_of_date=date(2026, 7, 25),
    )

    assert result == 25_000_000


def _event(
    *,
    event_type: CapitalActionEventType,
    effective_date: date,
    collected_at: datetime,
    amount_cny: float | None = None,
    shares: float | None = None,
    shares_before: float | None = None,
) -> CapitalActionEvent:
    key = (
        f"{event_type.value}:{effective_date.isoformat()}:{amount_cny}:"
        f"{shares}:{shares_before}"
    )
    digest = key.encode("utf-8").hex()[:64].ljust(64, "0")
    return CapitalActionEvent(
        event_key=digest,
        code="600000",
        name="浦发银行",
        event_type=event_type,
        announcement_at=datetime.combine(
            effective_date,
            datetime.min.time(),
            tzinfo=collected_at.tzinfo,
        ),
        effective_date=effective_date,
        amount_cny=amount_cny,
        shares=shares,
        shares_before=shares_before,
        confirmed_for_score=True,
        source_name="测试来源",
        source_url="https://example.com",
        source_record_id=digest,
        content_sha256=digest,
        collected_at=collected_at,
    )
