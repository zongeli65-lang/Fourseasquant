from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from fourseasquant.database import database_path, initialize_database
from fourseasquant.launch_agent import install_launch_agent_payload


LABEL = "com.fourseasquant.web"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DESTINATION = (
    Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
)


def launch_agent_definition(
    *,
    path: Path | None = None,
    repository_root: Path = REPOSITORY_ROOT,
    python_executable: Path | None = None,
) -> dict[str, Any]:
    selected_database = (path or database_path()).resolve()
    initialize_database(selected_database)
    logs_directory = repository_root / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_executable or Path(sys.executable)),
            "-m",
            "uvicorn",
            "--app-dir",
            str(repository_root / "backend"),
            "fourseasquant.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        "WorkingDirectory": str(repository_root),
        "EnvironmentVariables": {
            "FOURSEASQUANT_DB_PATH": str(selected_database),
            "FOURSEASQUANT_ENABLE_CORE_STRATEGY_AUTOMATION": "1",
            "FOURSEASQUANT_ENABLE_STARTUP_CATCHUP": "1",
            "FOURSEASQUANT_WEB_ORIGIN": "http://127.0.0.1:8000",
            "PYTHONPATH": str(repository_root / "backend"),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs_directory / "web.stdout.log"),
        "StandardErrorPath": str(logs_directory / "web.stderr.log"),
        "ProcessType": "Background",
    }


def render_launch_agent(**kwargs: Any) -> bytes:
    return plistlib.dumps(launch_agent_definition(**kwargs), sort_keys=True)


def install_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    (REPOSITORY_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    install_launch_agent_payload(
        destination,
        render_launch_agent(),
        load=lambda target: _launchctl("bootstrap", _domain(), str(target)),
        unload=lambda target: disable_launch_agent(
            target,
            ignore_errors=True,
        ),
    )


def enable_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    _launchctl("bootstrap", _domain(), str(destination))


def disable_launch_agent(
    destination: Path = DEFAULT_DESTINATION,
    *,
    ignore_errors: bool = False,
) -> None:
    subprocess.run(
        ["launchctl", "bootout", _domain(), str(destination)],
        check=not ignore_errors,
        capture_output=True,
        text=True,
    )


def uninstall_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    disable_launch_agent(destination, ignore_errors=True)
    destination.unlink(missing_ok=True)


def print_status() -> int:
    return subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"],
        check=False,
        text=True,
    ).returncode


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*arguments: str) -> None:
    subprocess.run(
        ["launchctl", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="管理 Fourseasquant 本机网站后台启动代理"
    )
    parser.add_argument(
        "command",
        choices=("install", "enable", "disable", "uninstall", "status", "render"),
    )
    args = parser.parse_args()
    if args.command == "install":
        install_launch_agent()
    elif args.command == "enable":
        enable_launch_agent()
    elif args.command == "disable":
        disable_launch_agent()
    elif args.command == "uninstall":
        uninstall_launch_agent()
    elif args.command == "status":
        return print_status()
    else:
        sys.stdout.buffer.write(render_launch_agent())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
