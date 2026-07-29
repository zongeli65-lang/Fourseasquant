from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from fourseasquant.database import initialize_database
from fourseasquant.launch_agent import (
    LABEL,
    install_launch_agent_payload,
    render_launch_agent,
)
from fourseasquant.industry_chain_launch_agent import (
    LABEL as INDUSTRY_CHAIN_LABEL,
    render_launch_agent as render_industry_chain_launch_agent,
)
from fourseasquant.ollama_launch_agent import (
    LABEL as OLLAMA_LABEL,
    render_launch_agent as render_ollama_launch_agent,
)
from fourseasquant.web_launch_agent import (
    LABEL as WEB_LABEL,
    render_launch_agent as render_web_launch_agent,
)
from fourseasquant.settings import SettingsUpdate, save_settings


def test_launch_agent_uses_periodic_unified_entrypoint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "launch-agent.db"
    repository = tmp_path / "repository"
    repository.mkdir()
    initialize_database(database)
    save_settings(
        database,
        SettingsUpdate(
            auto_update_time="16:45",
            benchmark="沪深 300",
            data_adapter="simulation",
            new_stock_exclusion_days=20,
        ),
    )

    definition = plistlib.loads(
        render_launch_agent(
            path=database,
            repository_root=repository,
            python_executable=Path("/example/python"),
        )
    )

    assert definition["Label"] == LABEL
    assert definition["StartInterval"] == 60
    assert definition["ProgramArguments"] == [
        "/example/python",
        "-m",
        "fourseasquant.scheduled_pipeline",
        "run-scheduled",
    ]
    assert definition["EnvironmentVariables"]["FOURSEASQUANT_DB_PATH"] == str(
        database.resolve()
    )
    assert "token" not in str(definition).lower()


def test_failed_install_restores_previous_launch_agent(tmp_path: Path) -> None:
    destination = tmp_path / f"{LABEL}.plist"
    previous = plistlib.dumps({"Label": LABEL, "StartInterval": 300})
    replacement = plistlib.dumps({"Label": LABEL, "StartInterval": 60})
    destination.write_bytes(previous)
    load_count = 0

    def load(_: Path) -> None:
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            raise RuntimeError("新计划加载失败")

    with pytest.raises(RuntimeError, match="新计划加载失败"):
        install_launch_agent_payload(
            destination,
            replacement,
            load=load,
            unload=lambda _: None,
        )

    assert destination.read_bytes() == previous
    assert load_count == 2


def test_industry_chain_launch_agent_runs_persistent_local_worker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "industry-chain-agent.db"
    repository = tmp_path / "repository"
    repository.mkdir()

    definition = plistlib.loads(
        render_industry_chain_launch_agent(
            path=database,
            repository_root=repository,
            python_executable=Path("/example/python"),
        )
    )

    assert definition["Label"] == INDUSTRY_CHAIN_LABEL
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True
    assert definition["ProgramArguments"] == [
        "/example/python",
        "-m",
        "fourseasquant.industry_chain_worker",
    ]
    assert definition["EnvironmentVariables"]["OLLAMA_NO_CLOUD"] == "true"
    assert "token" not in str(definition).lower()


def test_ollama_launch_agent_enforces_single_local_model(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    executable = tmp_path / "ollama"
    executable.touch()

    definition = plistlib.loads(
        render_ollama_launch_agent(
            repository_root=repository,
            ollama_executable=executable,
        )
    )

    assert definition["Label"] == OLLAMA_LABEL
    assert definition["ProgramArguments"] == [str(executable), "serve"]
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True
    assert definition["EnvironmentVariables"]["OLLAMA_HOST"] == (
        "127.0.0.1:11434"
    )
    assert definition["EnvironmentVariables"]["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert definition["EnvironmentVariables"]["OLLAMA_KEEP_ALIVE"] == "5m"


def test_web_launch_agent_serves_built_frontend_and_api_persistently(
    tmp_path: Path,
) -> None:
    database = tmp_path / "web-agent.db"
    repository = tmp_path / "repository"
    repository.mkdir()

    definition = plistlib.loads(
        render_web_launch_agent(
            path=database,
            repository_root=repository,
            python_executable=Path("/example/python"),
        )
    )

    assert definition["Label"] == WEB_LABEL
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True
    assert definition["ProgramArguments"] == [
        "/example/python",
        "-m",
        "uvicorn",
        "--app-dir",
        str(repository / "backend"),
        "fourseasquant.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    assert definition["EnvironmentVariables"]["FOURSEASQUANT_DB_PATH"] == str(
        database.resolve()
    )
    assert definition["EnvironmentVariables"]["PYTHONPATH"] == str(
        repository / "backend"
    )
    assert (
        definition["EnvironmentVariables"][
            "FOURSEASQUANT_ENABLE_CORE_STRATEGY_AUTOMATION"
        ]
        == "1"
    )
    assert "token" not in str(definition).lower()
