from __future__ import annotations

import pytest
from pytest import MonkeyPatch


@pytest.fixture(autouse=True)
def disable_real_keychain(monkeypatch: MonkeyPatch) -> None:
    """测试永远不能读取、写入或删除用户真实的 macOS 钥匙串。"""

    monkeypatch.setenv("FOURSEASQUANT_DISABLE_KEYCHAIN", "1")
