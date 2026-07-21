from __future__ import annotations

from datetime import date, timedelta


TRADING_DAYS_PER_YEAR = 252
TRADING_DAYS_PER_QUARTER = 63
CALENDAR_SUPPORTED_START = date(2023, 1, 1)
CALENDAR_SUPPORTED_END = date(2026, 12, 31)


SSE_HOLIDAY_RANGES = (
    (date(2023, 1, 2), date(2023, 1, 2)),
    (date(2023, 1, 23), date(2023, 1, 27)),
    (date(2023, 4, 5), date(2023, 4, 5)),
    (date(2023, 5, 1), date(2023, 5, 3)),
    (date(2023, 6, 22), date(2023, 6, 23)),
    (date(2023, 9, 29), date(2023, 10, 6)),
    (date(2024, 1, 1), date(2024, 1, 1)),
    (date(2024, 2, 9), date(2024, 2, 16)),
    (date(2024, 4, 4), date(2024, 4, 5)),
    (date(2024, 5, 1), date(2024, 5, 3)),
    (date(2024, 6, 10), date(2024, 6, 10)),
    (date(2024, 9, 16), date(2024, 9, 17)),
    (date(2024, 10, 1), date(2024, 10, 7)),
    (date(2025, 1, 1), date(2025, 1, 1)),
    (date(2025, 1, 28), date(2025, 2, 4)),
    (date(2025, 4, 4), date(2025, 4, 4)),
    (date(2025, 5, 1), date(2025, 5, 5)),
    (date(2025, 6, 2), date(2025, 6, 2)),
    (date(2025, 10, 1), date(2025, 10, 8)),
    (date(2026, 1, 1), date(2026, 1, 2)),
    (date(2026, 2, 16), date(2026, 2, 23)),
    (date(2026, 4, 6), date(2026, 4, 6)),
    (date(2026, 5, 1), date(2026, 5, 5)),
    (date(2026, 6, 19), date(2026, 6, 19)),
    (date(2026, 9, 25), date(2026, 9, 25)),
    (date(2026, 10, 1), date(2026, 10, 7)),
)


def _dates_between(start_date: date, end_date: date) -> set[date]:
    return {
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    }


SSE_HOLIDAYS = set().union(
    *(_dates_between(start, end) for start, end in SSE_HOLIDAY_RANGES)
)


def is_trading_day(candidate: date) -> bool:
    return candidate.weekday() < 5 and candidate not in SSE_HOLIDAYS


def trading_days_between(start_date: date, end_date: date) -> list[date]:
    return sorted(
        candidate
        for candidate in _dates_between(start_date, end_date)
        if is_trading_day(candidate)
    )
