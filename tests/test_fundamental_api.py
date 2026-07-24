from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from fourseasquant.database import initialize_database
from fourseasquant.discussion_sentiment import (
    DiscussionPost,
    aggregate_platform_discussion,
    combine_platform_aggregates,
)
from fourseasquant.fundamental_discovery import (
    BoardCandidate,
    BoardCandidateMember,
    BoardCandidateSnapshot,
)
from fourseasquant.fundamental_mechanical import (
    CapitalActionSignal,
    PersonalFundamentalMonthlySnapshot,
)
from fourseasquant.fundamental_repository import (
    save_board_candidate_snapshot,
    save_combined_discussion_signal,
    save_discussion_day,
    save_personal_fundamental_monthly_snapshot,
)
from fourseasquant.main import app


BEIJING = ZoneInfo("Asia/Shanghai")


def _monthly_snapshot(
    code: str,
    as_of_date: date,
    *,
    adjusted_pe: float | None = 10,
) -> PersonalFundamentalMonthlySnapshot:
    return PersonalFundamentalMonthlySnapshot(
        rules_version="personal-fundamental-v1",
        code=code,
        as_of_date=as_of_date,
        ordinary_pe=8,
        adjusted_pe=adjusted_pe,
        adjusted_pe_historical_percentile=50,
        adjusted_pe_peer_percentile=60,
        five_year_adjusted_eps_cagr=0.20,
        positive_growth_years=4,
        dividend_yield=0.02,
        lynch_growth_value_ratio=2.2,
        lynch_growth_value_label="good",
        net_cash_per_share=3,
        cash_adjusted_price=17,
        cash_adjusted_pe=8.5,
        short_term_interest_bearing_debt=100_000_000,
        long_term_interest_bearing_debt=100_000_000,
        debt_to_equity=0.2,
        short_term_debt_share=0.5,
        dividend_payout_ratio=0.16,
        consecutive_dividend_years=5,
        dividend_continuously_increased=True,
        free_cash_flow_per_share=2,
        price_to_free_cash_flow=10,
        inventory_status="available",
        inventory_growth=0.2,
        revenue_growth=0.3,
        inventory_growth_minus_revenue_growth=-0.1,
        pretax_margin=0.12,
        pretax_margin_historical_percentile=60,
        pretax_margin_peer_percentile=50,
        main_business_name="业务甲",
        main_business_profit_share=0.6,
        institution_holding_ratio=0.12,
        institution_holding_change=0.02,
        capital_action_signal=CapitalActionSignal(
            insider_net_purchase_amount=3_000_000,
            insider_net_purchase_ratio=0.003,
            insider_adjustment=30,
            cancelled_buyback_amount=10_000_000,
            cancelled_buyback_ratio=0.01,
            buyback_bonus=20,
            dilution_ratio=0.10,
            dilution_penalty=20,
            newly_issued_shares=10_000_000,
            shares_before_issuance=100_000_000,
        ),
        true_money_signal_score=80,
        floating_market_cap=1_000_000_000,
        floating_market_cap_percentile=66.67,
    )


def _seed_fundamentals(database: Path) -> None:
    initialize_database(database)
    collected_at = datetime(2026, 7, 24, 16, 30, tzinfo=BEIJING)
    save_board_candidate_snapshot(
        database,
        BoardCandidateSnapshot(
            source="eastmoney",
            effective_date=date(2026, 7, 24),
            complete=True,
            errors=[],
            boards=[
                BoardCandidate(
                    board_id="em:industry:BK001",
                    source_board_code="BK001",
                    name="银行",
                    kind="industry",
                    members=[
                        BoardCandidateMember(code="600000", name="浦发银行"),
                        BoardCandidateMember(code="000001", name="平安银行"),
                    ],
                ),
                BoardCandidate(
                    board_id="em:concept:BK002",
                    source_board_code="BK002",
                    name="高股息",
                    kind="concept",
                    members=[
                        BoardCandidateMember(code="600000", name="浦发银行"),
                    ],
                ),
            ],
        ),
        collected_at=collected_at,
    )
    first = _monthly_snapshot("600000", date(2026, 6, 30))
    second = first.model_copy(
        update={"as_of_date": date(2026, 7, 31), "adjusted_pe": 11}
    )
    save_personal_fundamental_monthly_snapshot(
        database,
        first,
        source_urls=["https://www.cninfo.com.cn/report-1"],
        created_at=datetime(2026, 6, 30, 16, 30, tzinfo=BEIJING),
    )
    save_personal_fundamental_monthly_snapshot(
        database,
        second,
        source_urls=["https://www.cninfo.com.cn/report-2"],
        created_at=datetime(2026, 7, 31, 16, 30, tzinfo=BEIJING),
    )

    discussion_time = datetime(2026, 7, 24, 10, tzinfo=BEIJING)
    eastmoney_post = DiscussionPost(
        platform="eastmoney_guba",
        post_id="em-1",
        code="600000",
        url="https://example.test/em-1",
        published_at=discussion_time,
        likes=12,
        text="利好",
        content_type="user_original",
    )
    xueqiu_post = DiscussionPost(
        platform="xueqiu",
        post_id="xq-1",
        code="600000",
        url="https://example.test/xq-1",
        published_at=discussion_time,
        likes=3,
        text="中性讨论",
        content_type="user_original",
    )
    eastmoney = aggregate_platform_discussion(
        [eastmoney_post],
        heat_universe=[1, 5, 10],
    )
    xueqiu = aggregate_platform_discussion(
        [xueqiu_post],
        heat_universe=[1, 5, 10],
    )
    save_discussion_day(
        database,
        posts=[eastmoney_post],
        aggregate=eastmoney,
        created_at=discussion_time,
    )
    save_discussion_day(
        database,
        posts=[xueqiu_post],
        aggregate=xueqiu,
        created_at=discussion_time,
    )
    combined = combine_platform_aggregates(eastmoney, xueqiu)
    assert combined is not None
    save_combined_discussion_signal(
        database,
        combined,
        created_at=discussion_time,
    )

    partial_time = datetime(2026, 7, 24, 11, tzinfo=BEIJING)
    partial_post = DiscussionPost(
        platform="eastmoney_guba",
        post_id="em-2",
        code="000001",
        url="https://example.test/em-2",
        published_at=partial_time,
        likes=None,
        text="等待另一平台",
        content_type="user_original",
    )
    partial = aggregate_platform_discussion(
        [partial_post],
        heat_universe=[1],
    )
    save_discussion_day(
        database,
        posts=[partial_post],
        aggregate=partial,
        created_at=partial_time,
    )


def test_fundamental_read_endpoints_expose_only_persisted_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "fundamental-api.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)

    with TestClient(app) as client:
        boards = client.get("/api/fundamentals/board-candidates/latest")
        board = client.get(
            "/api/fundamentals/board-candidates/em%3Aindustry%3ABK001"
        )
        latest_before_july = client.get(
            "/api/fundamentals/securities/600000/monthly/latest",
            params={"target_date": "2026-07-24"},
        )
        history = client.get(
            "/api/fundamentals/securities/600000/monthly",
            params={"limit": 12},
        )
        discussion = client.get(
            "/api/fundamentals/securities/600000/discussion/latest",
            params={"target_date": "2026-07-24"},
        )
        future_board_is_hidden = client.get(
            "/api/fundamentals/board-candidates/latest",
            params={"target_date": "2026-07-23"},
        )

    assert boards.status_code == 200
    assert boards.json()["snapshot"]["effective_date"] == "2026-07-24"
    assert boards.json()["snapshot"]["complete"] is True
    assert board.status_code == 200
    assert board.json()["name"] == "银行"
    assert latest_before_july.status_code == 200
    assert latest_before_july.json()["snapshot"]["as_of_date"] == "2026-06-30"
    assert latest_before_july.json()["source_urls"] == [
        "https://www.cninfo.com.cn/report-1"
    ]
    assert history.status_code == 200
    assert [item["snapshot"]["as_of_date"] for item in history.json()["records"]] == [
        "2026-07-31",
        "2026-06-30",
    ]
    assert history.json()["records"][0]["snapshot"]["net_cash_per_share"] == 3
    assert discussion.status_code == 200
    assert discussion.json()["eastmoney_guba"]["post_count"] == 1
    assert discussion.json()["xueqiu"]["post_count"] == 1
    assert discussion.json()["combined"] is not None
    assert discussion.json()["eastmoney_reference_count"] == 1
    assert future_board_is_hidden.status_code == 404


def test_latest_board_endpoint_uses_most_recent_available_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "latest-board-source.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)
    save_board_candidate_snapshot(
        database,
        BoardCandidateSnapshot(
            source="sina",
            effective_date=date(2026, 7, 24),
            complete=True,
            errors=[],
            boards=[
                BoardCandidate(
                    board_id="sina:industry:hangye_ZA01",
                    source_board_code="hangye_ZA01",
                    name="农业",
                    kind="industry",
                    members=[
                        BoardCandidateMember(code="600598", name="北大荒")
                    ],
                )
            ],
        ),
        collected_at=datetime(2026, 7, 24, 16, 31, tzinfo=BEIJING),
    )

    with TestClient(app) as client:
        latest = client.get("/api/fundamentals/board-candidates/latest")
        board = client.get(
            "/api/fundamentals/board-candidates/sina%3Aindustry%3Ahangye_ZA01"
        )

    assert latest.status_code == 200
    assert latest.json()["snapshot"]["source"] == "sina"
    assert board.status_code == 200
    assert board.json()["name"] == "农业"


def test_fundamental_overview_filters_searches_sorts_and_preserves_missing_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "fundamental-overview.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)

    with TestClient(app) as client:
        overview = client.get(
            "/api/fundamentals/overview",
            params={
                "target_date": "2026-07-24",
                "board_code": "BK001",
                "sort_by": "heat_percentile",
                "sort_order": "desc",
            },
        )
        searched = client.get(
            "/api/fundamentals/overview",
            params={"search": "浦发", "target_date": "2026-07-24"},
        )
        partial = client.get(
            "/api/fundamentals/securities/000001/discussion/latest"
        )

    assert overview.status_code == 200
    payload = overview.json()
    assert payload["selected_board_id"] == "em:industry:BK001"
    assert payload["total"] == 2
    assert [item["code"] for item in payload["items"]] == ["600000", "000001"]
    assert payload["items"][0]["data_status"] == "complete"
    assert payload["items"][1]["monthly"] is None
    assert payload["items"][1]["discussion"]["combined"] is None
    assert payload["items"][1]["data_status"] == "partial"
    assert searched.status_code == 200
    assert [item["name"] for item in searched.json()["items"]] == ["浦发银行"]
    assert partial.status_code == 200
    assert partial.json()["eastmoney_guba"] is not None
    assert partial.json()["xueqiu"] is None
    assert partial.json()["combined"] is None


def test_fundamental_endpoints_return_clear_empty_and_validation_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "empty-fundamentals.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))

    with TestClient(app) as client:
        boards = client.get("/api/fundamentals/board-candidates/latest")
        monthly = client.get(
            "/api/fundamentals/securities/600000/monthly/latest"
        )
        invalid = client.get(
            "/api/fundamentals/securities/not-a-code/monthly"
        )
        reversed_dates = client.get(
            "/api/fundamentals/securities/600000/discussion",
            params={"start_date": "2026-07-25", "end_date": "2026-07-24"},
        )
        overview = client.get("/api/fundamentals/overview")

    assert boards.status_code == 404
    assert boards.json()["detail"] == "尚无完整板块候选池快照"
    assert monthly.status_code == 404
    assert monthly.json()["detail"] == "该股票尚无月度基本面快照"
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "股票代码必须是六位数字"
    assert reversed_dates.status_code == 422
    assert reversed_dates.json()["detail"] == "舆情开始日期不能晚于结束日期"
    assert overview.status_code == 200
    assert overview.json()["items"] == []
    assert overview.json()["boards"] == []
