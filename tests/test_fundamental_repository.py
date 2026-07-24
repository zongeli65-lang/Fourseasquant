from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fourseasquant.database import initialize_database
from fourseasquant.discussion_sentiment import (
    DiscussionPost,
    aggregate_platform_discussion,
)
from fourseasquant.fundamental_mechanical import MonthlyFundamentalSnapshot
from fourseasquant.fundamental_repository import (
    count_discussion_post_references,
    read_latest_monthly_snapshot,
    save_discussion_day,
    save_monthly_snapshot,
    save_parsed_evidence,
)


def _monthly_snapshot(as_of_date: date) -> MonthlyFundamentalSnapshot:
    return MonthlyFundamentalSnapshot(
        rules_version="personal-fundamental-v1",
        code="600000",
        as_of_date=as_of_date,
        ordinary_pe=8,
        adjusted_pe=10,
        adjusted_pe_historical_percentile=50,
        adjusted_pe_peer_percentile=50,
        five_year_adjusted_eps_cagr=0.20,
        positive_growth_years=4,
        dividend_yield=0.02,
        lynch_growth_value_ratio=2.2,
        lynch_growth_value_label="good",
        net_cash_per_share=3,
        cash_adjusted_price=17,
        cash_adjusted_pe=8.5,
        debt_to_equity=0.2,
        short_term_debt_share=0.5,
        dividend_payout_ratio=0.16,
        consecutive_dividend_years=5,
        free_cash_flow_per_share=2,
        price_to_free_cash_flow=10,
        inventory_growth_minus_revenue_growth=-0.1,
        pretax_margin=0.12,
        main_business_name="业务甲",
        main_business_profit_share=0.6,
        institution_holding_ratio=0.12,
        institution_holding_change=0.02,
        true_money_signal_score=80,
        floating_market_cap=1_000_000_000,
        floating_market_cap_percentile=66.67,
    )


def test_monthly_storage_reuses_evidence_and_persists_only_changed_fields(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fundamental.db"
    initialize_database(database)
    stored_at = datetime(
        2026,
        6,
        30,
        16,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )

    first_evidence = save_parsed_evidence(
        database,
        source_url="https://www.cninfo.com.cn/report.pdf",
        content_sha256="a" * 64,
        parsed_payload={"扣非归母净利润": 200_000_000},
        first_seen_at=stored_at,
    )
    duplicate_evidence = save_parsed_evidence(
        database,
        source_url="https://www.cninfo.com.cn/report.pdf",
        content_sha256="a" * 64,
        parsed_payload={"扣非归母净利润": 200_000_000},
        first_seen_at=stored_at,
    )
    first_snapshot = _monthly_snapshot(date(2026, 6, 30))
    first_save = save_monthly_snapshot(
        database,
        first_snapshot,
        source_urls=["https://www.cninfo.com.cn/report.pdf"],
        created_at=stored_at,
    )
    second_snapshot = first_snapshot.model_copy(
        update={
            "as_of_date": date(2026, 7, 31),
            "ordinary_pe": 8.4,
            "adjusted_pe": 10.5,
            "cash_adjusted_price": 18,
            "cash_adjusted_pe": 9,
            "floating_market_cap": 1_050_000_000,
        }
    )
    second_save = save_monthly_snapshot(
        database,
        second_snapshot,
        source_urls=["https://www.cninfo.com.cn/report.pdf"],
        created_at=stored_at,
    )

    assert first_evidence.inserted is True
    assert duplicate_evidence.inserted is False
    assert first_save.stored is True
    assert "net_cash_per_share" in first_save.changed_fields
    assert second_save.changed_fields == {
        "adjusted_pe": 10.5,
        "cash_adjusted_pe": 9,
        "cash_adjusted_price": 18,
        "floating_market_cap": 1_050_000_000,
        "ordinary_pe": 8.4,
    }
    assert read_latest_monthly_snapshot(database, "600000") == second_snapshot


def test_discussion_storage_keeps_only_references_and_purges_after_thirty_days(
    tmp_path: Path,
) -> None:
    database = tmp_path / "discussion.db"
    initialize_database(database)
    old_time = datetime(
        2026,
        6,
        1,
        10,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    current_time = datetime(
        2026,
        7,
        24,
        10,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    old_post = DiscussionPost(
        platform="xueqiu",
        post_id="old",
        code="600000",
        url="https://example.test/old",
        published_at=old_time,
        likes=1,
        text="旧的利好讨论正文不应入库",
    )
    current_post = DiscussionPost(
        platform="xueqiu",
        post_id="current",
        code="600000",
        url="https://example.test/current",
        published_at=current_time,
        likes=3,
        text="新的利好讨论正文也不应入库",
    )
    old_aggregate = aggregate_platform_discussion(
        [old_post],
        heat_universe=[1],
    )
    current_aggregate = aggregate_platform_discussion(
        [current_post, current_post],
        heat_universe=[1],
    )

    save_discussion_day(
        database,
        posts=[old_post],
        aggregate=old_aggregate,
        created_at=old_time,
    )
    save_discussion_day(
        database,
        posts=[current_post, current_post],
        aggregate=current_aggregate,
        created_at=current_time,
    )

    assert count_discussion_post_references(database) == 1
