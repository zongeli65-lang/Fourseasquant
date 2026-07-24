from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.fundamental_discovery import (  # noqa: E402
    collect_eastmoney_board_snapshot,
)
from fourseasquant.fundamental_repository import save_board_snapshot  # noqa: E402


def main() -> int:
    path = database_path()
    initialize_database(path)
    industry_catalog = ak.stock_board_industry_name_em()
    concept_catalog = ak.stock_board_concept_name_em()
    industry_codes = {
        str(value) for value in industry_catalog["板块代码"].tolist()
    }

    def fetch_constituents(board_code: str) -> pd.DataFrame:
        if board_code in industry_codes:
            return cast(
                pd.DataFrame,
                ak.stock_board_industry_cons_em(symbol=board_code),
            )
        return cast(
            pd.DataFrame,
            ak.stock_board_concept_cons_em(symbol=board_code),
        )

    snapshot = collect_eastmoney_board_snapshot(
        industry_catalog=industry_catalog,
        concept_catalog=concept_catalog,
        constituent_fetcher=fetch_constituents,
        effective_date=date.today(),
        max_workers=4,
    )
    if not snapshot.complete:
        print(snapshot.model_dump_json(indent=2), flush=True)
        return 1
    saved = save_board_snapshot(
        path,
        snapshot,
        collected_at=datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    print(
        json.dumps(
            {
                "source": snapshot.source,
                "effective_date": snapshot.effective_date.isoformat(),
                "board_count": len(snapshot.boards),
                "member_count": sum(
                    len(board.members) for board in snapshot.boards
                ),
                "inserted": saved.inserted,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
