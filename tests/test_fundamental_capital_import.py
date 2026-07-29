from datetime import date

import pandas as pd

from fourseasquant.fundamental_candidates import (
    codes_announced_on_or_before,
)


def test_candidate_announcement_codes_exclude_future_and_unknown_dates() -> None:
    frame = pd.DataFrame(
        [
            {"证券代码": "600000", "公告日期": "2026-07-24"},
            {"证券代码": "000001", "公告日期": "2026-07-25"},
            {"证券代码": "300001", "公告日期": None},
        ]
    )

    codes = codes_announced_on_or_before(
        frame,
        code_column="证券代码",
        date_column="公告日期",
        as_of_date=date(2026, 7, 24),
    )

    assert codes == ["600000"]
