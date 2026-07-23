from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

import akshare as ak  # type: ignore[import-untyped]  # noqa: E402

from fourseasquant.akshare_history import (  # noqa: E402
    HISTORY_QFQ_SOURCE,
    build_akshare_one_year_history_importer,
    build_akshare_qfq_history_importer,
)
from fourseasquant.candlesticks import (  # noqa: E402
    import_index_candles,
    publish_complete_candle_dates,
)
from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.settings import read_settings  # noqa: E402


def report_progress(completed: int, total: int, code: str, succeeded: bool) -> None:
    if succeeded and completed % 25 != 0 and completed != total:
        return
    label = "成功" if succeeded else "失败"
    print(f"[{completed}/{total}] {code} {label}", flush=True)


def main() -> int:
    path = database_path()
    initialize_database(path)
    settings = read_settings(path)
    requested_end_date = date.today()
    qfq_source = f"{HISTORY_QFQ_SOURCE}:{requested_end_date.isoformat()}"
    raw = build_akshare_one_year_history_importer(
        max_workers=2,
        progress_callback=report_progress,
        include_technical_boards=True,
    ).import_one_year(
        path=path,
        requested_end_date=requested_end_date,
        new_stock_exclusion_days=settings.new_stock_exclusion_days,
        warmup_trading_days=60,
    )
    if raw.failed_codes:
        print(json.dumps({"stage": "raw", "failed_codes": raw.failed_codes}))
        return 1
    adjusted = build_akshare_qfq_history_importer(
        max_workers=2,
        progress_callback=report_progress,
        version_tag=requested_end_date.isoformat(),
        include_technical_boards=True,
    ).import_one_year(
        path=path,
        requested_end_date=requested_end_date,
        new_stock_exclusion_days=settings.new_stock_exclusion_days,
        warmup_trading_days=60,
    )
    if adjusted.failed_codes:
        print(
            json.dumps(
                {"stage": "qfq", "failed_codes": adjusted.failed_codes},
                ensure_ascii=False,
            )
        )
        return 1
    indexes = import_index_candles(
        path,
        requested_end_date=requested_end_date,
        index_history=lambda symbol: ak.stock_zh_index_daily(symbol=symbol),
        warmup_trading_days=60,
    )
    published_days = publish_complete_candle_dates(
        path,
        qfq_source=qfq_source,
        publication_start=_one_year_start(adjusted.range_end),
    )
    print(
        json.dumps(
            {
                "range_start": adjusted.range_start.isoformat(),
                "range_end": adjusted.range_end.isoformat(),
                "symbols": adjusted.completed_symbols,
                "indexes": indexes,
                "published_days": published_days,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _one_year_start(range_end: date) -> date:
    try:
        previous_year = range_end.replace(year=range_end.year - 1)
    except ValueError:
        previous_year = range_end.replace(year=range_end.year - 1, day=28)
    return previous_year + timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(main())
