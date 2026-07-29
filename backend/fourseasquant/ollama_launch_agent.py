from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from fourseasquant.industry_chain_launch_agent import REPOSITORY_ROOT
from fourseasquant.launch_agent import install_launch_agent_payload


LABEL = "com.fourseasquant.ollama"
DEFAULT_OLLAMA_EXECUTABLE = Path("/opt/homebrew/bin/ollama")
DEFAULT_DESTINATION = (
    Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
)


def launch_agent_definition(
    *,
    repository_root: Path = REPOSITORY_ROOT,
    ollama_executable: Path = DEFAULT_OLLAMA_EXECUTABLE,
) -> dict[str, Any]:
    if not ollama_executable.exists():
        raise FileNotFoundError(f"未找到 Ollama：{ollama_executable}")
    logs_directory = repository_root / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [str(ollama_executable), "serve"],
        "WorkingDirectory": str(repository_root),
        "EnvironmentVariables": {
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_NO_CLOUD": "true",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_CONTEXT_LENGTH": "8192",
            "OLLAMA_KV_CACHE_TYPE": "f16",
            "OLLAMA_KEEP_ALIVE": "5m",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs_directory / "ollama.stdout.log"),
        "StandardErrorPath": str(logs_directory / "ollama.stderr.log"),
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
        description="管理产业链本地模型后台启动代理"
    )
    parser.add_argument(
        "command",
        choices=("install", "disable", "uninstall", "status", "render"),
    )
    args = parser.parse_args()
    if args.command == "install":
        install_launch_agent()
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
