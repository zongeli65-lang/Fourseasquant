from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    environment = os.environ.copy()
    environment.setdefault(
        "FOURSEASQUANT_DB_PATH",
        str(REPOSITORY_ROOT / "data" / "fourseasquant.db"),
    )
    environment.setdefault("FOURSEASQUANT_ENABLE_STARTUP_CATCHUP", "1")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--app-dir",
            "backend",
            "fourseasquant.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            environment.get("FOURSEASQUANT_API_PORT", "8000"),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
