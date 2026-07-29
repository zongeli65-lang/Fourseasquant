from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev import build_backend_command


def test_development_backend_disables_reload_by_default() -> None:
    command = build_backend_command({})

    assert "--reload" not in command


def test_development_backend_allows_explicit_reload() -> None:
    command = build_backend_command({"FOURSEASQUANT_RELOAD": "1"})

    assert command[-1] == "--reload"
