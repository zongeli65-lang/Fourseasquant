from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from collections.abc import Callable
from typing import Any

from fourseasquant.database import database_path, initialize_database


LABEL = "com.fourseasquant.daily"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DESTINATION = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


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
            "fourseasquant.scheduled_pipeline",
            "run-scheduled",
        ],
        "WorkingDirectory": str(repository_root),
        "EnvironmentVariables": {
            "FOURSEASQUANT_DB_PATH": str(selected_database),
            "PYTHONPATH": str(repository_root / "backend"),
        },
        "StartInterval": 60,
        "StandardOutPath": str(logs_directory / "launchd.stdout.log"),
        "StandardErrorPath": str(logs_directory / "launchd.stderr.log"),
        "ProcessType": "Background",
    }


def render_launch_agent(**kwargs: Any) -> bytes:
    return plistlib.dumps(launch_agent_definition(**kwargs), sort_keys=True)


def install_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    (REPOSITORY_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    payload = render_launch_agent()
    install_launch_agent_payload(
        destination,
        payload,
        load=lambda target: _launchctl("bootstrap", _domain(), str(target)),
        unload=lambda target: disable_launch_agent(target, ignore_errors=True),
    )


def install_launch_agent_payload(
    destination: Path,
    payload: bytes,
    *,
    load: Callable[[Path], None],
    unload: Callable[[Path], None],
) -> None:
    plistlib.loads(payload)
    previous_payload = destination.read_bytes() if destination.exists() else None
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    unload(destination)
    temporary_path.replace(destination)
    try:
        load(destination)
    except Exception:
        unload(destination)
        if previous_payload is None:
            destination.unlink(missing_ok=True)
        else:
            _write_atomically(destination, previous_payload)
            try:
                load(destination)
            except Exception:
                pass
        raise


def enable_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    _launchctl("bootstrap", _domain(), str(destination))


def disable_launch_agent(
    destination: Path = DEFAULT_DESTINATION,
    *,
    ignore_errors: bool = False,
) -> None:
    command = ["launchctl", "bootout", _domain(), str(destination)]
    subprocess.run(
        command,
        check=not ignore_errors,
        capture_output=True,
        text=True,
    )


def uninstall_launch_agent(destination: Path = DEFAULT_DESTINATION) -> None:
    disable_launch_agent(destination, ignore_errors=True)
    destination.unlink(missing_ok=True)


def print_status() -> int:
    result = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"],
        check=False,
        text=True,
    )
    return result.returncode


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _write_atomically(destination: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    temporary_path.replace(destination)


def _launchctl(*arguments: str) -> None:
    subprocess.run(
        ["launchctl", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="管理 Fourseasquant LaunchAgent")
    parser.add_argument("command", choices=("install", "enable", "disable", "uninstall", "status", "render"))
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
