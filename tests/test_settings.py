from __future__ import annotations

import pytest
from pydantic import ValidationError

from fourseasquant.settings import SettingsUpdate


def test_automatic_update_window_cannot_cross_midnight() -> None:
    with pytest.raises(
        ValidationError,
        match="自动更新时间不得晚于 18:59",
    ):
        SettingsUpdate(
            auto_update_time="19:00",
            benchmark="沪深 300",
            data_adapter="simulation",
            new_stock_exclusion_days=60,
        )
