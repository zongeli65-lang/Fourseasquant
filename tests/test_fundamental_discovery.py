from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from fourseasquant.database import initialize_database
from fourseasquant.fundamental_discovery import (
    collect_eastmoney_board_candidate_snapshot,
)
from fourseasquant.fundamental_repository import (
    read_latest_board_candidate_snapshot,
    save_board_candidate_snapshot,
)


def test_eastmoney_industry_and_concept_boards_keep_their_memberships() -> None:
    industry_catalog = pd.DataFrame(
        [{"板块名称": "银行", "板块代码": "BK0475"}]
    )
    concept_catalog = pd.DataFrame(
        [{"板块名称": "高股息", "板块代码": "BK1234"}]
    )
    constituents = {
        "BK0475": pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行"},
                {"代码": "000001", "名称": "平安银行"},
            ]
        ),
        "BK1234": pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行"},
                {"代码": "600900", "名称": "长江电力"},
            ]
        ),
    }

    snapshot = collect_eastmoney_board_candidate_snapshot(
        industry_catalog=industry_catalog,
        concept_catalog=concept_catalog,
        constituent_fetcher=lambda board_code: constituents[board_code],
        effective_date=date(2026, 7, 24),
        max_workers=2,
    )

    assert snapshot.complete is True
    assert snapshot.source == "eastmoney"
    assert [board.board_id for board in snapshot.boards] == [
        "em:concept:BK1234",
        "em:industry:BK0475",
    ]
    assert snapshot.boards[0].members[0].code == "600000"
    assert snapshot.boards[1].members[1].name == "平安银行"


def test_failed_board_fetch_makes_candidate_snapshot_incomplete() -> None:
    industry_catalog = pd.DataFrame(
        [{"板块名称": "银行", "板块代码": "BK0475"}]
    )

    def fail(_: str) -> pd.DataFrame:
        raise RuntimeError("上游不可用")

    snapshot = collect_eastmoney_board_candidate_snapshot(
        industry_catalog=industry_catalog,
        concept_catalog=pd.DataFrame(columns=["板块名称", "板块代码"]),
        constituent_fetcher=fail,
        effective_date=date(2026, 7, 24),
    )

    assert snapshot.complete is False
    assert snapshot.boards == []
    assert snapshot.errors == ["BK0475: 上游不可用"]


def test_complete_board_candidate_snapshot_is_content_deduplicated(
    tmp_path: Path,
) -> None:
    database = tmp_path / "boards.db"
    initialize_database(database)
    snapshot = collect_eastmoney_board_candidate_snapshot(
        industry_catalog=pd.DataFrame(
            [{"板块名称": "银行", "板块代码": "BK0475"}]
        ),
        concept_catalog=pd.DataFrame(columns=["板块名称", "板块代码"]),
        constituent_fetcher=lambda _: pd.DataFrame(
            [{"代码": "600000", "名称": "浦发银行"}]
        ),
        effective_date=date(2026, 7, 24),
    )
    collected_at = datetime(
        2026,
        7,
        24,
        16,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )

    first = save_board_candidate_snapshot(
        database,
        snapshot,
        collected_at=collected_at,
    )
    duplicate = save_board_candidate_snapshot(
        database,
        snapshot,
        collected_at=collected_at,
    )

    assert first.inserted is True
    assert duplicate.inserted is False
    assert read_latest_board_candidate_snapshot(database, "eastmoney") == snapshot
