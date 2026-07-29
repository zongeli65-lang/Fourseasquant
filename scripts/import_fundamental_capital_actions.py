from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar, cast
from zoneinfo import ZoneInfo

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.akshare_history import HISTORY_SOURCE  # noqa: E402
from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.cninfo_announcement import (  # noqa: E402
    enrich_cancelled_buyback_announcements,
)
from fourseasquant.fundamental_candidates import (  # noqa: E402
    build_fundamental_candidate_pool,
    codes_announced_on_or_before,
)
from fourseasquant.fundamental_capital_actions import (  # noqa: E402
    collect_capital_action_snapshot,
)
from fourseasquant.fundamental_repository import (  # noqa: E402
    read_latest_board_candidate_snapshot,
    save_capital_action_snapshot,
)
from fourseasquant.sqlite_connection import open_database_connection  # noqa: E402


Result = TypeVar("Result")
BEIJING = ZoneInfo("Asia/Shanghai")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="采集并原子发布候选股票的真实资本行为"
    )
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--workers", type=int, default=6)
    arguments = parser.parse_args()
    _disable_application_proxy_environment()
    path = database_path()
    initialize_database(path)
    as_of_date = arguments.as_of or _latest_market_date(path)
    latest_market_date = _latest_market_date(path)
    if as_of_date != latest_market_date:
        print(
            json.dumps(
                {
                    "as_of_date": as_of_date.isoformat(),
                    "published": False,
                    "error": (
                        "巨潮高管增减持接口只能证明运行日最近一年窗口完整；"
                        "资本行为仅允许发布最新正式行情日 "
                        f"{latest_market_date.isoformat()}"
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    collected_at = datetime.now(BEIJING)

    increases = _retry(
        "巨潮高管增持",
        lambda: cast(
            pd.DataFrame,
            ak.stock_hold_management_detail_cninfo(symbol="增持"),
        ),
    )
    decreases = _retry(
        "巨潮高管减持",
        lambda: cast(
            pd.DataFrame,
            ak.stock_hold_management_detail_cninfo(symbol="减持"),
        ),
    )
    latest_share_changes = _retry(
        "巨潮全市场股本变动",
        lambda: cast(
            pd.DataFrame,
            ak.stock_hold_change_cninfo(symbol="全部"),
        ),
    )
    repurchases = _retry(
        "东方财富回购实施汇总",
        lambda: cast(pd.DataFrame, ak.stock_repurchase_em()),
    )
    codes = (
        sorted({str(code).zfill(6) for code in arguments.codes})
        if arguments.codes
        else _candidate_codes(
            path,
            as_of_date=as_of_date,
            increases=increases,
            decreases=decreases,
            latest_share_changes=latest_share_changes,
            repurchases=repurchases,
        )
    )
    start_date = _one_year_before(as_of_date).strftime("%Y%m%d")
    end_date = as_of_date.strftime("%Y%m%d")
    snapshot = collect_capital_action_snapshot(
        codes=codes,
        as_of_date=as_of_date,
        collected_at=collected_at,
        management_increases=increases,
        management_decreases=decreases,
        repurchases=repurchases,
        share_change_fetcher=lambda code: _retry(
            f"{code} 巨潮公司股本变动",
            lambda: cast(
                pd.DataFrame,
                ak.stock_share_change_cninfo(
                    symbol=code,
                    start_date="20000101",
                    end_date=end_date,
                ),
            ),
        ),
        announcement_fetcher=lambda code: _retry(
            f"{code} 巨潮回购公告",
            lambda: enrich_cancelled_buyback_announcements(
                cast(
                    pd.DataFrame,
                    ak.stock_zh_a_disclosure_report_cninfo(
                        symbol=code,
                        market="沪深京",
                        keyword="回购",
                        start_date=start_date,
                        end_date=end_date,
                    ),
                ),
                storage_directory=path.parent / "raw" / "cninfo",
            ),
        ),
        management_coverage_complete=(
            collected_at.date() == as_of_date
        ),
        max_workers=arguments.workers,
    )
    result = save_capital_action_snapshot(
        path,
        snapshot,
        collected_at=collected_at,
    )
    print(
        json.dumps(
            {
                "as_of_date": as_of_date.isoformat(),
                "expected": len(snapshot.expected_codes),
                "completed": len(snapshot.completed_codes),
                "events": len(snapshot.events),
                "failed": len(snapshot.errors),
                "published": result.published,
                "batch_id": result.batch_id,
                "errors": snapshot.errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if result.published else 1


def _candidate_codes(
    path: Path,
    *,
    as_of_date: date,
    increases: pd.DataFrame,
    decreases: pd.DataFrame,
    latest_share_changes: pd.DataFrame,
    repurchases: pd.DataFrame,
) -> list[str]:
    market_codes = _market_codes(path, as_of_date)
    announcement_codes = [
        *codes_announced_on_or_before(
            increases,
            code_column="证券代码",
            date_column="公告日期",
            as_of_date=as_of_date,
        ),
        *codes_announced_on_or_before(
            decreases,
            code_column="证券代码",
            date_column="公告日期",
            as_of_date=as_of_date,
        ),
        *codes_announced_on_or_before(
            latest_share_changes,
            code_column="证券代码",
            date_column="公告日期",
            as_of_date=as_of_date,
        ),
        *codes_announced_on_or_before(
            repurchases,
            code_column="股票代码",
            date_column="最新公告日期",
            as_of_date=as_of_date,
        ),
    ]
    boards = read_latest_board_candidate_snapshot(
        path,
        "sina",
        effective_on_or_before=as_of_date,
    )
    board_members = (
        {
            board.board_id: [member.code for member in board.members]
            for board in boards.boards
        }
        if boards is not None
        else {}
    )
    return build_fundamental_candidate_pool(
        as_of_date=as_of_date,
        heat_codes=_heat_codes(path, as_of_date),
        technical_codes=_technical_codes(path, as_of_date),
        announcement_codes=announcement_codes,
        board_members=board_members,
        market_codes=market_codes,
    ).codes


def _market_codes(path: Path, as_of_date: date) -> list[str]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code
            FROM historical_security_facts
            WHERE source = ? AND actual_data_date = ?
            ORDER BY code
            """,
            (HISTORY_SOURCE, as_of_date.isoformat()),
        ).fetchall()
    return [cast(str, row[0]) for row in rows]


def _technical_codes(path: Path, as_of_date: date) -> list[str]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code
            FROM technical_daily_scores
            WHERE actual_data_date = (
                SELECT MAX(actual_data_date)
                FROM technical_daily_scores
                WHERE actual_data_date <= ?
            )
            ORDER BY total_score DESC, code
            LIMIT 400
            """,
            (as_of_date.isoformat(),),
        ).fetchall()
    return [cast(str, row[0]) for row in rows]


def _heat_codes(path: Path, as_of_date: date) -> list[str]:
    with open_database_connection(path) as connection:
        rows = connection.execute(
            """
            SELECT code, MAX(COALESCE(heat_percentile, raw_heat)) AS heat
            FROM discussion_daily_aggregates
            WHERE actual_date = (
                SELECT MAX(actual_date)
                FROM discussion_daily_aggregates
                WHERE actual_date <= ?
            )
            GROUP BY code
            ORDER BY heat DESC, code
            LIMIT 400
            """,
            (as_of_date.isoformat(),),
        ).fetchall()
    return [cast(str, row[0]) for row in rows]


def _latest_market_date(path: Path) -> date:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM historical_security_facts
            WHERE source = ?
            """,
            (HISTORY_SOURCE,),
        ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("尚无正式行情，不能采集资本行为")
    return date.fromisoformat(cast(str, row[0]))


def _one_year_before(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _disable_application_proxy_environment() -> None:
    for key in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def _retry(
    label: str,
    operation: Callable[[], Result],
    *,
    attempts: int = 4,
) -> Result:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break
            delay = min(8.0, float(2 ** (attempt - 1)))
            print(f"{label} 请求失败，{delay:.0f} 秒后重试。", flush=True)
            time.sleep(delay)
    assert last_error is not None
    raise last_error


if __name__ == "__main__":
    raise SystemExit(main())
