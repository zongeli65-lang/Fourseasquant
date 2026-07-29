from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar, cast
from zoneinfo import ZoneInfo

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.akshare_history import HISTORY_SOURCE  # noqa: E402
from fourseasquant.fundamental_discovery import (  # noqa: E402
    BoardKind,
    collect_sina_board_candidate_snapshot,
)
from fourseasquant.fundamental_repository import (  # noqa: E402
    save_board_candidate_snapshot,
)
from fourseasquant.sqlite_connection import open_database_connection  # noqa: E402

Result = TypeVar("Result")
BEIJING = ZoneInfo("Asia/Shanghai")
PROXY_ENVIRONMENT_KEYS = (
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
)


def main() -> int:
    _disable_application_proxy_environment()
    path = database_path()
    initialize_database(path)
    eligible_codes = _latest_eligible_codes(path)
    industry_catalog = _retry(
        "新浪行业板块目录",
        lambda: ak.stock_sector_spot(indicator="行业"),
    )
    concept_catalog = _retry(
        "新浪概念板块目录",
        lambda: ak.stock_sector_spot(indicator="概念"),
    )

    def fetch_constituents(
        board_code: str,
        _board_kind: BoardKind,
    ) -> pd.DataFrame:
        frame = cast(
            pd.DataFrame,
            _retry(
                f"新浪板块 {board_code}",
                lambda: ak.stock_sector_detail(sector=board_code),
            ),
        )
        normalized = frame.rename(columns={"code": "代码", "name": "名称"})
        normalized["代码"] = normalized["代码"].astype(str).str.zfill(6)
        return normalized[normalized["代码"].isin(eligible_codes)]

    now = datetime.now(BEIJING)
    snapshot = collect_sina_board_candidate_snapshot(
        industry_catalog=industry_catalog,
        concept_catalog=concept_catalog,
        constituent_fetcher=fetch_constituents,
        effective_date=now.date(),
        max_workers=4,
    )
    if not snapshot.complete:
        print(snapshot.model_dump_json(indent=2), flush=True)
        return 1
    saved = save_board_candidate_snapshot(
        path,
        snapshot,
        collected_at=now,
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


def _disable_application_proxy_environment() -> None:
    """保留 TUN，同时避免 Requests 使用本机应用层 HTTP 代理。"""
    for key in PROXY_ENVIRONMENT_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def _latest_eligible_codes(path: Path) -> set[str]:
    with open_database_connection(path) as connection:
        latest_date = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM historical_security_facts
            WHERE source = ?
            """,
            (HISTORY_SOURCE,),
        ).fetchone()[0]
        if latest_date is None:
            raise RuntimeError("尚无正式股票行情，不能确定板块候选范围")
        rows = connection.execute(
            """
            SELECT code
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            """,
            (HISTORY_SOURCE, latest_date),
        ).fetchall()
    return {cast(str, row[0]) for row in rows}


def _retry(
    label: str,
    operation: Callable[[], Result],
    *,
    attempts: int = 6,
) -> Result:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            delay = min(12.0, 1.5 * (2 ** (attempt - 1)))
            print(
                f"{label} 第 {attempt} 次请求失败，{delay:.1f} 秒后重试。",
                flush=True,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


if __name__ == "__main__":
    raise SystemExit(main())
