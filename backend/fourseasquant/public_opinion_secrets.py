from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


ApiKeySource = Literal[
    "runtime",
    "environment",
    "keychain",
    "unconfigured",
]
KEYCHAIN_SERVICE = "com.fourseasquant.deepseek"
KEYCHAIN_ACCOUNT = "fourseasquant"
SECURITY_EXECUTABLE = "/usr/bin/security"


class DeepSeekApiConfigurationStatus(BaseModel):
    configured: bool
    source: ApiKeySource
    transient: bool
    updated_at: datetime | None


_lock = threading.Lock()
_runtime_api_key = ""
_runtime_updated_at: datetime | None = None
_runtime_persisted = False


def get_deepseek_api_key() -> str:
    with _lock:
        runtime_api_key = _runtime_api_key
    if runtime_api_key:
        return runtime_api_key
    environment_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if environment_api_key:
        return environment_api_key
    return _read_keychain_api_key()


def read_deepseek_api_configuration() -> DeepSeekApiConfigurationStatus:
    with _lock:
        runtime_api_key = _runtime_api_key
        runtime_updated_at = _runtime_updated_at
        runtime_persisted = _runtime_persisted
    if runtime_api_key:
        return DeepSeekApiConfigurationStatus(
            configured=True,
            source="keychain" if runtime_persisted else "runtime",
            transient=not runtime_persisted,
            updated_at=runtime_updated_at,
        )
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return DeepSeekApiConfigurationStatus(
            configured=True,
            source="environment",
            transient=False,
            updated_at=None,
        )
    if _read_keychain_api_key():
        return DeepSeekApiConfigurationStatus(
            configured=True,
            source="keychain",
            transient=False,
            updated_at=None,
        )
    return DeepSeekApiConfigurationStatus(
        configured=False,
        source="unconfigured",
        transient=True,
        updated_at=None,
    )


def set_deepseek_runtime_api_key(
    api_key: str,
    *,
    updated_at: datetime,
) -> DeepSeekApiConfigurationStatus:
    normalized = api_key.strip()
    if len(normalized) < 16:
        raise ValueError("API 密钥长度不足")
    if len(normalized) > 512:
        raise ValueError("API 密钥长度超过限制")
    if any(character.isspace() for character in normalized):
        raise ValueError("API 密钥不能包含空白字符")
    if updated_at.tzinfo is None:
        raise ValueError("更新时间必须包含时区")
    persisted = False
    if _keychain_enabled():
        _store_keychain_api_key(normalized)
        persisted = True
    global _runtime_api_key, _runtime_updated_at, _runtime_persisted
    with _lock:
        _runtime_api_key = normalized
        _runtime_updated_at = updated_at
        _runtime_persisted = persisted
    return read_deepseek_api_configuration()


def clear_deepseek_runtime_api_key() -> DeepSeekApiConfigurationStatus:
    if _keychain_enabled():
        _delete_keychain_api_key()
    clear_deepseek_runtime_cache()
    return read_deepseek_api_configuration()


def clear_deepseek_runtime_cache() -> None:
    global _runtime_api_key, _runtime_updated_at, _runtime_persisted
    with _lock:
        _runtime_api_key = ""
        _runtime_updated_at = None
        _runtime_persisted = False


def _keychain_enabled() -> bool:
    return (
        sys.platform == "darwin"
        and os.environ.get("FOURSEASQUANT_DISABLE_KEYCHAIN", "0") != "1"
        and os.path.exists(SECURITY_EXECUTABLE)
    )


def _read_keychain_api_key() -> str:
    if not _keychain_enabled():
        return ""
    try:
        result = subprocess.run(
            [
                SECURITY_EXECUTABLE,
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _store_keychain_api_key(api_key: str) -> None:
    try:
        result = subprocess.run(
            [
                SECURITY_EXECUTABLE,
                "add-generic-password",
                "-U",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            input=f"{api_key}\n",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("macOS 钥匙串保存失败") from error
    if result.returncode != 0:
        raise RuntimeError("macOS 钥匙串保存失败")


def _delete_keychain_api_key() -> None:
    if not _read_keychain_api_key():
        return
    try:
        result = subprocess.run(
            [
                SECURITY_EXECUTABLE,
                "delete-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("macOS 钥匙串清除失败") from error
    if result.returncode != 0:
        raise RuntimeError("macOS 钥匙串清除失败")
