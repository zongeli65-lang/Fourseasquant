from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from fourseasquant.core_strategy import (
    CandidatePipelineDecision,
    FundamentalInvestigationTarget,
)
from fourseasquant.core_strategy_execution import EntryPlanningDecision
from fourseasquant.core_strategy_positions import (
    HoldingPosition,
    PositionManagementDecision,
)
from fourseasquant.core_strategy_repository import (
    CoreStrategyDaySnapshot,
    CoreStrategyInputVersions,
    CoreStrategyPortfolioSnapshot,
    PortfolioPositionMark,
    publish_core_strategy_day,
    publish_core_strategy_portfolio,
    publish_strategy_fundamental_targets,
    read_core_strategy_day,
    read_core_strategy_portfolio,
    read_latest_core_strategy_day,
    read_latest_core_strategy_portfolio,
    read_latest_strategy_fundamental_targets,
    read_strategy_fundamental_targets,
)


BEIJING = ZoneInfo("Asia/Shanghai")
ACTUAL_DATE = date(2026, 7, 29)
VERSION = "core-strategy-v1"


def _target(
    code: str = "600000",
    *,
    rank: int = 1,
    version: str = VERSION,
    actual_date: date = ACTUAL_DATE,
) -> FundamentalInvestigationTarget:
    return FundamentalInvestigationTarget(
        actual_date=actual_date,
        strategy_version=version,
        code=code,
        name=f"股票{code}",
        preliminary_rank=rank,
        reason="missing",
        required_metrics=["adjusted_pe", "main_business_profit_share"],
        missing_metrics=["adjusted_pe", "main_business_profit_share"],
    )


def _day_snapshot(
    *,
    version: str = VERSION,
    actual_date: date = ACTUAL_DATE,
    publication_blocked: bool = False,
) -> CoreStrategyDaySnapshot:
    return CoreStrategyDaySnapshot(
        actual_date=actual_date,
        strategy_version=version,
        market_state="rising",
        input_versions=CoreStrategyInputVersions(
            market_environment="market-environment-v1",
            technical_scores="technical-v3",
            industry_chain="industry-chain-v1",
            fundamental="personal-fundamental-v1",
            public_opinion="public-opinion-deepseek-v1",
        ),
        candidate_pipeline=CandidatePipelineDecision(
            actual_date=actual_date,
            strategy_version=version,
            candidates=[],
            fundamental_targets=[],
        ),
        entry_planning=EntryPlanningDecision(
            actual_date=actual_date,
            strategy_version=version,
            maximum_positions=1,
            full_position_slot=40_000,
            candidates=[],
            orders=[],
            remaining_cash=40_000,
        ),
        position_management=PositionManagementDecision(
            actual_date=actual_date,
            strategy_version=version,
            publication_blocked=publication_blocked,
            decisions=[],
            positions=[],
            orders=[],
        ),
    )


def _position() -> HoldingPosition:
    return HoldingPosition(
        code="600000",
        name="浦发银行",
        shares=1_000,
        cost_price=9.8,
        stop_price=9,
        pressure_target=12,
        initial_risk=0.8,
        highest_close_since_entry=10,
    )


def _portfolio_snapshot(
    *,
    actual_date: date = ACTUAL_DATE,
    version: str = VERSION,
) -> CoreStrategyPortfolioSnapshot:
    return CoreStrategyPortfolioSnapshot(
        actual_date=actual_date,
        strategy_version=version,
        initial_capital=100_000,
        available_cash=90_000,
        net_asset_value=100_000,
        positions=[_position()],
        marks=[
            PortfolioPositionMark(
                code="600000",
                price=10,
                mark_date=actual_date,
                source="daily_close",
            )
        ],
    )


def test_complete_strategy_day_round_trips_as_immutable_snapshot(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    snapshot = _day_snapshot()
    published_at = datetime(2026, 7, 29, 19, tzinfo=BEIJING)

    published = publish_core_strategy_day(
        database,
        snapshot=snapshot,
        published_at=published_at,
    )
    stored = read_core_strategy_day(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
    )

    assert stored == published
    assert stored is not None
    assert stored.snapshot.input_versions.technical_scores == "technical-v3"


def test_strategy_day_requires_new_version_for_changed_content(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    published_at = datetime(2026, 7, 29, 19, tzinfo=BEIJING)
    snapshot = _day_snapshot()
    publish_core_strategy_day(
        database,
        snapshot=snapshot,
        published_at=published_at,
    )

    changed = snapshot.model_copy(
        update={
            "input_versions": CoreStrategyInputVersions(
                market_environment="market-environment-v2",
                technical_scores="technical-v3",
            )
        }
    )
    with pytest.raises(ValueError, match="修改结果必须使用新策略版本"):
        publish_core_strategy_day(
            database,
            snapshot=changed,
            published_at=published_at,
        )


def test_blocked_position_result_cannot_form_complete_strategy_snapshot() -> None:
    with pytest.raises(ValidationError, match="公司行为不完整"):
        _day_snapshot(publication_blocked=True)


def test_latest_strategy_reader_uses_actual_date(tmp_path: Path) -> None:
    database = tmp_path / "strategy.db"
    for day in (date(2026, 7, 28), ACTUAL_DATE):
        publish_core_strategy_day(
            database,
            snapshot=_day_snapshot(actual_date=day),
            published_at=datetime(
                day.year,
                day.month,
                day.day,
                19,
                tzinfo=BEIJING,
            ),
        )

    stored = read_latest_core_strategy_day(
        database,
        as_of_date=ACTUAL_DATE,
    )

    assert stored is not None
    assert stored.snapshot.actual_date == ACTUAL_DATE


def test_complete_portfolio_round_trips_after_strategy_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    published_at = datetime(2026, 7, 29, 19, tzinfo=BEIJING)
    publish_core_strategy_day(
        database,
        snapshot=_day_snapshot(),
        published_at=published_at,
    )

    published = publish_core_strategy_portfolio(
        database,
        snapshot=_portfolio_snapshot(),
        published_at=published_at,
    )
    stored = read_core_strategy_portfolio(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
    )
    latest = read_latest_core_strategy_portfolio(
        database,
        as_of_date=ACTUAL_DATE,
    )

    assert stored == published
    assert latest == published
    assert stored is not None
    assert stored.snapshot.positions[0].code == "600000"


def test_portfolio_requires_complete_and_balanced_marks() -> None:
    with pytest.raises(ValidationError, match="每只持仓"):
        CoreStrategyPortfolioSnapshot(
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            initial_capital=100_000,
            available_cash=90_000,
            net_asset_value=100_000,
            positions=[_position()],
            marks=[],
        )
    with pytest.raises(ValidationError, match="净值必须等于"):
        CoreStrategyPortfolioSnapshot.model_validate(
            _portfolio_snapshot().model_dump()
            | {"net_asset_value": 99_999}
        )


def test_portfolio_cannot_publish_without_matching_strategy_day(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="已经发布"):
        publish_core_strategy_portfolio(
            tmp_path / "strategy.db",
            snapshot=_portfolio_snapshot(),
            published_at=datetime(
                2026,
                7,
                29,
                19,
                tzinfo=BEIJING,
            ),
        )


def test_target_snapshot_round_trips_ordered_investigation_contract(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    published_at = datetime(2026, 7, 29, 18, 30, tzinfo=BEIJING)
    targets = [_target(), _target("000001", rank=2)]

    published = publish_strategy_fundamental_targets(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        targets=targets,
        published_at=published_at,
    )
    stored = read_strategy_fundamental_targets(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
    )

    assert published.targets == targets
    assert stored is not None
    assert stored.published_at == published_at
    assert [target.code for target in stored.targets] == ["600000", "000001"]
    assert stored.targets[0].required_metrics == [
        "adjusted_pe",
        "main_business_profit_share",
    ]


def test_same_date_and_version_is_idempotent_but_not_mutable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    first_time = datetime(2026, 7, 29, 18, 30, tzinfo=BEIJING)
    second_time = datetime(2026, 7, 29, 18, 40, tzinfo=BEIJING)
    targets = [_target()]

    first = publish_strategy_fundamental_targets(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        targets=targets,
        published_at=first_time,
    )
    repeated = publish_strategy_fundamental_targets(
        database,
        actual_date=ACTUAL_DATE,
        strategy_version=VERSION,
        targets=targets,
        published_at=second_time,
    )

    assert repeated == first
    with pytest.raises(ValueError, match="修改目标必须使用新策略版本"):
        publish_strategy_fundamental_targets(
            database,
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            targets=[_target("000001")],
            published_at=second_time,
        )


@pytest.mark.parametrize(
    "target",
    [
        _target(actual_date=date(2026, 7, 28)),
        _target(version="core-strategy-v2"),
    ],
)
def test_snapshot_rejects_target_with_mismatched_identity(
    tmp_path: Path,
    target: FundamentalInvestigationTarget,
) -> None:
    with pytest.raises(ValueError, match="必须与目标快照"):
        publish_strategy_fundamental_targets(
            tmp_path / "strategy.db",
            actual_date=ACTUAL_DATE,
            strategy_version=VERSION,
            targets=[target],
            published_at=datetime(2026, 7, 29, 18, 30, tzinfo=BEIJING),
        )


def test_latest_reader_uses_actual_date_before_publication_time(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strategy.db"
    for day in (date(2026, 7, 28), ACTUAL_DATE):
        publish_strategy_fundamental_targets(
            database,
            actual_date=day,
            strategy_version=VERSION,
            targets=[_target(actual_date=day)],
            published_at=datetime(
                day.year,
                day.month,
                day.day,
                18,
                30,
                tzinfo=BEIJING,
            ),
        )

    stored = read_latest_strategy_fundamental_targets(
        database,
        as_of_date=ACTUAL_DATE,
    )

    assert stored is not None
    assert stored.actual_date == ACTUAL_DATE


def test_missing_database_has_no_targets(tmp_path: Path) -> None:
    assert (
        read_strategy_fundamental_targets(
            tmp_path / "missing.db",
            actual_date=ACTUAL_DATE,
        )
        is None
    )
