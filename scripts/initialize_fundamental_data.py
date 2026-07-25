from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="初始化完整资本行为批次和月度基本面快照"
    )
    parser.add_argument("--codes", nargs="*", default=[])
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--workers", type=int, default=4)
    arguments = parser.parse_args()

    shared_arguments: list[str] = []
    if arguments.as_of is not None:
        shared_arguments.extend(["--as-of", arguments.as_of.isoformat()])
    if arguments.codes:
        shared_arguments.extend(["--codes", *arguments.codes])
    capital = subprocess.run(
        [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_fundamental_capital_actions.py"
            ),
            *shared_arguments,
            "--workers",
            str(max(1, arguments.workers)),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if capital.returncode != 0:
        return capital.returncode
    monthly = subprocess.run(
        [
            sys.executable,
            str(
                REPOSITORY_ROOT
                / "scripts"
                / "import_fundamental_monthly_akshare.py"
            ),
            *shared_arguments,
            "--workers",
            str(max(1, arguments.workers)),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return monthly.returncode


if __name__ == "__main__":
    raise SystemExit(main())
