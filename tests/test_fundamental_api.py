from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import fourseasquant.main as main_module
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
from fourseasquant.fundamental_capital_actions import (
    CapitalActionEvent,
    CapitalActionEventType,
    CapitalActionSnapshot,
)
from fourseasquant.fundamental_mechanical import (
    CapitalActionSignal,
    PersonalFundamentalMonthlySnapshot,
)
from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    LynchFinancialBase,
    calculate_lynch_daily_result,
    rank_lynch_results,
)
from fourseasquant.fundamental_lynch_repository import save_lynch_daily_batch
from fourseasquant.fundamental_automation import FundamentalAutomationOutcome
from fourseasquant.fundamental_repository import (
    FundamentalUpdateAttempt,
    save_board_candidate_snapshot,
    save_capital_action_snapshot,
    save_combined_discussion_signal,
    save_discussion_day,
    save_personal_fundamental_monthly_snapshot,
    save_fundamental_update_attempt,
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
    legacy = second.model_copy(
        update={
            "as_of_date": date(2026, 8, 31),
            "rules_version": "personal-fundamental-v0",
            "adjusted_pe": 99,
        }
    )
    save_personal_fundamental_monthly_snapshot(
        database,
        legacy,
        source_urls=["https://www.cninfo.com.cn/legacy"],
        created_at=datetime(2026, 8, 31, 16, 30, tzinfo=BEIJING),
    )
    legacy_only = _monthly_snapshot(
        "900001",
        date(2026, 6, 30),
    ).model_copy(update={"rules_version": "personal-fundamental-v0"})
    save_personal_fundamental_monthly_snapshot(
        database,
        legacy_only,
        source_urls=["https://www.cninfo.com.cn/legacy-only"],
        created_at=datetime(2026, 7, 31, 16, 30, tzinfo=BEIJING),
    )
    digest = "c" * 64
    save_capital_action_snapshot(
        database,
        CapitalActionSnapshot(
            as_of_date=date(2026, 7, 24),
            expected_codes=["600000"],
            completed_codes=["600000"],
            errors={},
            events=[
                CapitalActionEvent(
                    event_key=digest,
                    code="600000",
                    name="浦发银行",
                    event_type=CapitalActionEventType.insider_buy,
                    announcement_at=datetime(
                        2026,
                        7,
                        20,
                        tzinfo=BEIJING,
                    ),
                    effective_date=date(2026, 7, 19),
                    shares=10_000,
                    amount_cny=125_000,
                    price_cny=12.5,
                    reason="竞价交易",
                    confirmed_for_score=True,
                    source_name="巨潮资讯",
                    source_url="https://www.cninfo.com.cn/capital-1",
                    source_record_id=digest,
                    content_sha256=digest,
                    collected_at=collected_at,
                )
            ],
        ),
        collected_at=collected_at,
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
        capital_actions = client.get(
            "/api/fundamentals/securities/600000/capital-actions",
            params={"target_date": "2026-07-24"},
        )
        capital_status = client.get(
            "/api/fundamentals/capital-actions/status",
            params={"target_date": "2026-07-24"},
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
    assert capital_actions.status_code == 200
    assert capital_actions.json()["as_of_date"] == "2026-07-24"
    assert capital_actions.json()["events"][0]["amount_cny"] == 125_000
    assert capital_status.status_code == 200
    assert capital_status.json()["status"] == "ready"
    assert capital_status.json()["expected_count"] == 1
    assert capital_status.json()["confirmed_event_count"] == 1


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


def test_capital_action_status_reports_failed_attempt_and_keeps_last_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "capital-action-status.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)
    failed_at = datetime(2026, 7, 25, 16, 31, tzinfo=BEIJING)
    save_capital_action_snapshot(
        database,
        CapitalActionSnapshot(
            as_of_date=date(2026, 7, 25),
            expected_codes=["000001", "600000"],
            completed_codes=["600000"],
            events=[],
            errors={"000001": "TimeoutError: 巨潮请求超时"},
        ),
        collected_at=failed_at,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/fundamentals/capital-actions/status",
            params={"target_date": "2026-07-25"},
        )
        evidence = client.get(
            "/api/fundamentals/securities/600000/capital-actions",
            params={"target_date": "2026-07-25"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["publication_date"] == "2026-07-24"
    assert response.json()["latest_attempt_at"] == failed_at.isoformat()
    assert response.json()["failure_stage"] == "资本行为采集"
    assert response.json()["errors"] == {
        "000001": "TimeoutError: 巨潮请求超时"
    }
    assert evidence.status_code == 200
    assert evidence.json()["as_of_date"] == "2026-07-24"


def test_first_capital_action_failure_is_visible_without_a_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "first-capital-failure.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    initialize_database(database)
    save_capital_action_snapshot(
        database,
        CapitalActionSnapshot(
            as_of_date=date(2026, 7, 25),
            expected_codes=["000001", "600000"],
            completed_codes=["600000"],
            events=[],
            errors={"000001": "巨潮请求超时"},
        ),
        collected_at=datetime(2026, 7, 25, 16, 31, tzinfo=BEIJING),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/fundamentals/capital-actions/status",
            params={"target_date": "2026-07-25"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["publication_date"] is None
    assert response.json()["failure_stage"] == "资本行为采集"
    assert response.json()["expected_count"] == 2
    assert response.json()["completed_count"] == 1


def test_monthly_failure_overrides_ready_capital_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "monthly-failure-status.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)
    save_fundamental_update_attempt(
        database,
        FundamentalUpdateAttempt(
            target_date=date(2026, 7, 24),
            stage="monthly_snapshot",
            status="failed",
            attempted_at=datetime(2026, 7, 24, 17, 0, tzinfo=BEIJING),
            error_summary="覆盖 199/200",
        ),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/fundamentals/capital-actions/status",
            params={"target_date": "2026-07-24"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["publication_date"] == "2026-07-24"
    assert response.json()["failure_stage"] == "月度基本面快照"
    assert response.json()["errors"] == {"__global__": "覆盖 199/200"}


def test_capital_action_retry_endpoint_runs_forced_fundamental_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "capital-action-retry.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    initialize_database(database)
    calls: list[bool] = []

    def run_retry(*, force: bool = False) -> FundamentalAutomationOutcome:
        calls.append(force)
        return FundamentalAutomationOutcome(
            status="succeeded",
            target_date=date(2026, 7, 25),
            stage="complete",
            reason="资本行为已发布",
        )

    monkeypatch.setattr(
        main_module,
        "run_scheduled_fundamental_update",
        run_retry,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/fundamentals/capital-actions/retry"
        )

    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
    assert calls == [True]


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
        all_overview = client.get(
            "/api/fundamentals/overview",
            params={"target_date": "2026-07-24", "limit": 100},
        )

    assert overview.status_code == 200
    payload = overview.json()
    assert payload["selected_board_id"] == "em:industry:BK001"
    assert payload["total"] == 2
    assert [item["code"] for item in payload["items"]] == ["600000", "000001"]
    assert payload["items"][0]["data_status"] == "complete"
    assert all_overview.status_code == 200
    assert "900001" not in {
        item["code"] for item in all_overview.json()["items"]
    }
    assert payload["items"][1]["monthly"] is None
    assert payload["items"][1]["discussion"]["combined"] is None
    assert payload["items"][1]["data_status"] == "partial"
    assert searched.status_code == 200
    assert [item["name"] for item in searched.json()["items"]] == ["浦发银行"]
    assert partial.status_code == 200
    assert partial.json()["eastmoney_guba"] is not None
    assert partial.json()["xueqiu"] is None
    assert partial.json()["combined"] is None


def test_fundamental_overview_contains_lynch_results_and_uses_boards_as_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "fundamental-unified-overview.db"
    monkeypatch.setenv("FOURSEASQUANT_DB_PATH", str(database))
    _seed_fundamentals(database)
    target = date(2026, 7, 24)
    calculated_at = datetime(2026, 7, 24, 16, 31, tzinfo=BEIJING)
    results = rank_lynch_results(
        [
            calculate_lynch_daily_result(
                LynchFinancialBase(
                    code=code,
                    name=name,
                    financial_as_of=date(2025, 12, 31),
                    latest_notice_date=None,
                    annual_adjusted_eps=[
                        AnnualAdjustedEps(year=2023, value=1.0),
                        AnnualAdjustedEps(year=2024, value=1.2),
                        AnnualAdjustedEps(year=2025, value=1.44),
                    ],
                    ttm_adjusted_eps=1.5,
                    prior_ttm_adjusted_eps=1.4,
                    ttm_dividend_per_share=0.2,
                    audit_status="standard_unqualified",
                ),
                target_date=target,
                close=close,
            )
            for code, name, close in [
                ("600000", "浦发银行", 10.0),
                ("300001", "特锐德", 20.0),
            ]
        ]
    )
    save_lynch_daily_batch(
        database,
        target_date=target,
        financial_base_date=target,
        expected_codes=["600000", "300001"],
        results=results,
        errors={},
        calculated_at=calculated_at,
    )

    with TestClient(app) as client:
        all_stocks = client.get(
            "/api/fundamentals/overview",
            params={
                "target_date": target.isoformat(),
                "sort_by": "lynch_ratio",
                "sort_order": "desc",
                "limit": 100,
            },
        )
        board_filter = client.get(
            "/api/fundamentals/overview",
            params={
                "target_date": target.isoformat(),
                "board_code": "BK001",
                "limit": 100,
            },
        )

    assert all_stocks.status_code == 200
    payload = all_stocks.json()
    assert payload["lynch_total_count"] == 2
    assert payload["lynch_calculable_count"] == 2
    assert payload["lynch_actual_data_date"] == "2026-07-24"
    by_code = {item["code"]: item for item in payload["items"]}
    assert by_code["300001"]["name"] == "特锐德"
    assert by_code["300001"]["lynch"]["lynch_ratio"] is not None
    assert "em:industry:BK001" in by_code["600000"]["board_ids"]
    assert board_filter.status_code == 200
    assert board_filter.json()["total"] == 1
    filtered_codes = {
        item["code"] for item in board_filter.json()["items"]
    }
    assert "600000" in filtered_codes
    assert "300001" not in filtered_codes


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
