from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="初始化全市场林奇财务基座并生成首个日频结果"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--workers", type=int, default=12)
    arguments = parser.parse_args()
    selected_date = arguments.as_of or date.today()
    environment = os.environ.copy()
    base = subprocess.run(
        [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_lynch_financial_base_akshare.py"
            ),
            "--as-of",
            selected_date.isoformat(),
            "--workers",
            str(arguments.workers),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    if base.returncode != 0:
        return base.returncode
    daily = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "calculate_lynch_daily.py"),
            "--target-date",
            selected_date.isoformat(),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    return daily.returncode


if __name__ == "__main__":
    raise SystemExit(main())
