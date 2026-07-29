from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.akshare_history import (  # noqa: E402
    build_akshare_one_year_history_importer,
)
from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.settings import read_settings  # noqa: E402


def report_progress(completed: int, total: int, code: str, succeeded: bool) -> None:
    if succeeded and completed % 25 != 0 and completed != total:
        return
    status = "成功" if succeeded else "失败"
    print(f"[{completed}/{total}] {code} {status}", flush=True)


def main() -> int:
    path = database_path()
    initialize_database(path)
    settings = read_settings(path)
    summary = build_akshare_one_year_history_importer(
        max_workers=2,
        progress_callback=report_progress,
    ).import_one_year(
        path=path,
        requested_end_date=date.today(),
        new_stock_exclusion_days=settings.new_stock_exclusion_days,
    )
    print(
        json.dumps(
            {
                "range_start": summary.range_start.isoformat(),
                "range_end": summary.range_end.isoformat(),
                "total_symbols": summary.total_symbols,
                "completed_symbols": summary.completed_symbols,
                "failed_codes": summary.failed_codes,
                "published_days": summary.published_days,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if not summary.failed_codes else 1


if __name__ == "__main__":
    raise SystemExit(main())
