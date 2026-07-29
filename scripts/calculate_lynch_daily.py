from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from fourseasquant.database import database_path, initialize_database  # noqa: E402
from fourseasquant.fundamental_lynch_service import (  # noqa: E402
    run_lynch_daily_calculation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="全市场林奇比日频重算")
    parser.add_argument("--target-date", type=date.fromisoformat, default=None)
    arguments = parser.parse_args()
    path = database_path()
    initialize_database(path)
    result = run_lynch_daily_calculation(
        path, arguments.target_date or date.today()
    )
    print(
        json.dumps(
            {
                "batch_id": result.batch_id,
                "published": result.published,
                "expected": result.expected_count,
                "completed": result.completed_count,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if result.published else 1


if __name__ == "__main__":
    raise SystemExit(main())
