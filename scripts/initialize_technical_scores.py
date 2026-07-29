from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.akshare_history import HISTORY_QFQ_SOURCE  # noqa: E402
from fourseasquant.candle_refresh import (  # noqa: E402
    refresh_one_year_candles,
)
from fourseasquant.database import (  # noqa: E402
    database_path,
    initialize_database,
)
from fourseasquant.sqlite_connection import open_database_connection  # noqa: E402
from fourseasquant.technical_scoring import (  # noqa: E402
    ALGORITHM_VERSION,
    score_technical_history,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="初始化三市场个股技术评分"
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date.today(),
        help="目标截止日期，格式 YYYY-MM-DD",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="AKShare 股票历史并发数",
    )
    parser.add_argument(
        "--skip-market-refresh",
        action="store_true",
        help="复用数据库中已存在的完整行情版本",
    )
    return parser.parse_args()


def _progress(completed: int, total: int, code: str, succeeded: bool) -> None:
    if succeeded and completed % 50 != 0 and completed != total:
        return
    label = "成功" if succeeded else "失败"
    print(f"[{completed}/{total}] {code} {label}", flush=True)


def main() -> int:
    arguments = _arguments()
    path = database_path()
    initialize_database(path)
    requested_end = arguments.end_date
    if arguments.skip_market_refresh:
        actual_end = _latest_qfq_end(path)
        qfq_source = _latest_qfq_source(path, actual_end)
    else:
        refreshed = refresh_one_year_candles(
            path,
            requested_end_date=requested_end,
            max_workers=max(1, arguments.workers),
            warmup_trading_days=60,
            progress_callback=_progress,
        )
        actual_end = refreshed.actual_data_date
        qfq_source = (
            f"{HISTORY_QFQ_SOURCE}:{requested_end.isoformat()}"
        )
    publication = score_technical_history(
        path,
        official_start=_one_year_start(actual_end),
        official_end=actual_end,
        qfq_source=qfq_source,
        version=ALGORITHM_VERSION,
    )
    print(
        json.dumps(
            publication.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


def _latest_qfq_end(path: Path) -> date:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT MAX(actual_data_date)
            FROM candle_dataset_publications
            """
        ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("数据库中没有已发布的完整 K 线版本")
    return date.fromisoformat(str(row[0]))


def _latest_qfq_source(path: Path, actual_end: date) -> str:
    with open_database_connection(path) as connection:
        row = connection.execute(
            """
            SELECT qfq_source
            FROM candle_dataset_publications
            WHERE actual_data_date = ?
            """,
            (actual_end.isoformat(),),
        ).fetchone()
    if row is None:
        raise RuntimeError("数据库中没有目标日期的前复权版本")
    return str(row[0])


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
