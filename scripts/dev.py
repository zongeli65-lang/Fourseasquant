from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def start_process(command: list[str], environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    environment = os.environ.copy()
    api_port = environment.get("FOURSEASQUANT_API_PORT", "8000")
    web_port = environment.get("FOURSEASQUANT_WEB_PORT", "5173")
    environment["FOURSEASQUANT_API_ORIGIN"] = f"http://127.0.0.1:{api_port}"
    environment["FOURSEASQUANT_WEB_ORIGIN"] = f"http://127.0.0.1:{web_port}"
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "--app-dir",
        "backend",
        "fourseasquant.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        api_port,
    ]
    if environment.get("FOURSEASQUANT_RELOAD", "1") == "1":
        backend_command.append("--reload")
    environment.setdefault(
        "FOURSEASQUANT_DB_PATH", str(REPOSITORY_ROOT / "data" / "fourseasquant.db")
    )
    processes = [
        start_process(
            backend_command,
            environment,
        ),
        start_process(
            [
                "npm",
                "run",
                "dev",
                "--workspace",
                "frontend",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                web_port,
            ],
            environment,
        ),
    ]

    stopping = False

    def request_stop(_: int, __: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        while not stopping:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code
            time.sleep(0.1)
    finally:
        for process in reversed(processes):
            stop_process(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
