from __future__ import annotations

import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from pytest import MonkeyPatch

import fourseasquant.public_opinion_secrets as secrets


BEIJING = ZoneInfo("Asia/Shanghai")


def test_keychain_configuration_survives_runtime_cache_reset(
    monkeypatch: MonkeyPatch,
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(secrets, "_keychain_enabled", lambda: True)
    monkeypatch.setattr(
        secrets,
        "_store_keychain_api_key",
        lambda value: stored.update(api_key=value),
    )
    monkeypatch.setattr(
        secrets,
        "_read_keychain_api_key",
        lambda: stored.get("api_key", ""),
    )
    monkeypatch.setattr(
        secrets,
        "_delete_keychain_api_key",
        lambda: stored.clear(),
    )
    secrets.clear_deepseek_runtime_cache()
    configured = secrets.set_deepseek_runtime_api_key(
        "sk-persistent-local-test-secret",
        updated_at=datetime(2026, 7, 29, 17, tzinfo=BEIJING),
    )
    secrets.clear_deepseek_runtime_cache()
    restored = secrets.read_deepseek_api_configuration()
    key = secrets.get_deepseek_api_key()
    cleared = secrets.clear_deepseek_runtime_api_key()

    assert configured.source == "keychain"
    assert configured.transient is False
    assert restored.source == "keychain"
    assert restored.transient is False
    assert key == "sk-persistent-local-test-secret"
    assert cleared.configured is False


def test_keychain_storage_does_not_expose_secret_in_process_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    api_key = "sk-private-process-argument-test"
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(secrets, "_keychain_enabled", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    secrets.clear_deepseek_runtime_cache()

    configured = secrets.set_deepseek_runtime_api_key(
        api_key,
        updated_at=datetime(2026, 7, 29, 18, tzinfo=BEIJING),
    )

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert api_key not in command
    assert command[-1] == "-w"
    assert kwargs["input"] == f"{api_key}\n"
    assert configured.source == "keychain"
