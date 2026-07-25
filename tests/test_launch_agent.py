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
