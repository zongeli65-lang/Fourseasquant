from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from fourseasquant.database import initialize_database
from fourseasquant.fundamental_lynch import (
    AnnualAdjustedEps,
    LynchFinancialBase,
)
from fourseasquant.fundamental_lynch_automation import (
    run_scheduled_lynch_update,
)
from fourseasquant.fundamental_lynch_repository import (
    save_lynch_financial_base_batch,
)
from fourseasquant.fundamental_lynch_service import (
    run_lynch_daily_calculation,
)


def test_automation_initializes_base_then_publishes_daily_and_skips_repeat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "automation.db"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO historical_security_facts (
                source, actual_data_date, code, name, open, high, low, close,
                previous_close, change_pct, volume, turnover_cny,
                listing_trading_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "akshare_sina_daily",
                "2026-07-24",
                "600000",
                "浦发银行",
                10,
                10,
                10,
                10,
                10,
                0,
                1,
                1,
                120,
            ),
        )
    commands: list[str] = []

    def run_command(command: list[str], selected_path: Path) -> int:
        name = Path(command[1]).name
        commands.append(name)
        if name == "import_lynch_financial_base_akshare.py":
            save_lynch_financial_base_batch(
                selected_path,
                target_date=date(2026, 7, 24),
                expected_codes=["600000"],
                bases=[
                    LynchFinancialBase(
                        code="600000",
                        name="浦发银行",
                        financial_as_of=date(2026, 3, 31),
                        latest_notice_date=date(2026, 4, 30),
                        annual_adjusted_eps=[
                            AnnualAdjustedEps(year=2023, value=1),
                            AnnualAdjustedEps(year=2024, value=1.2),
                            AnnualAdjustedEps(year=2025, value=1.4),
                        ],
                        ttm_adjusted_eps=1.5,
                        prior_ttm_adjusted_eps=1.3,
                        ttm_dividend_per_share=0.2,
                    )
                ],
                errors={},
                collected_at=datetime(2026, 7, 24, 16, 30),
            )
        else:
            run_lynch_daily_calculation(
                selected_path,
                date(2026, 7, 24),
                calculated_at=datetime(2026, 7, 24, 16, 31),
            )
        return 0

    first = run_scheduled_lynch_update(
        path=path,
        target_date=date(2026, 7, 24),
        command_runner=run_command,
    )
    second = run_scheduled_lynch_update(
        path=path,
        target_date=date(2026, 7, 24),
        command_runner=run_command,
    )

    assert first.status == "succeeded"
    assert second.status == "skipped"
    assert commands == [
        "import_lynch_financial_base_akshare.py",
        "calculate_lynch_daily.py",
    ]
