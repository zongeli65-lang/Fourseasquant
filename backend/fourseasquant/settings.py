from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, field_validator

from fourseasquant.sqlite_connection import open_database_connection

Benchmark = Literal["沪深 300", "中证 500"]
DataAdapter = Literal["simulation", "simulation_conservative"]
BENCHMARK_OPTIONS: list[Benchmark] = ["沪深 300", "中证 500"]
DATA_ADAPTER_OPTIONS: list[DataAdapter] = [
    "simulation",
    "simulation_conservative",
]


class SettingsUpdate(BaseModel):
    auto_update_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    benchmark: Benchmark
    data_adapter: DataAdapter
    new_stock_exclusion_days: int = Field(ge=0, le=365)

    @field_validator("auto_update_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        hour, minute = (int(part) for part in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("自动更新时间必须是有效的 24 小时时间")
        if (hour, minute) > (18, 59):
            raise ValueError(
                "自动更新时间不得晚于 18:59，避免五小时重试窗口跨日"
            )
        return value


class SettingsResponse(SettingsUpdate):
    benchmark_options: list[Benchmark]
    data_adapter_options: list[DataAdapter]


def _response(settings: SettingsUpdate) -> SettingsResponse:
    return SettingsResponse(
        **settings.model_dump(),
        benchmark_options=BENCHMARK_OPTIONS,
        data_adapter_options=DATA_ADAPTER_OPTIONS,
    )


def read_settings(path: Path) -> SettingsResponse:
    with open_database_connection(path) as connection:
        row = cast(
            tuple[str, str, str, int],
            connection.execute(
                """
                SELECT auto_update_time, benchmark, data_adapter,
                       new_stock_exclusion_days
                FROM app_settings
                WHERE id = 1
                """
            ).fetchone(),
        )
    return _response(
        SettingsUpdate.model_validate(
            {
                "auto_update_time": row[0],
                "benchmark": row[1],
                "data_adapter": row[2],
                "new_stock_exclusion_days": row[3],
            }
        )
    )


def save_settings(path: Path, settings: SettingsUpdate) -> SettingsResponse:
    with open_database_connection(path) as connection:
        connection.execute(
            """
            UPDATE app_settings
            SET auto_update_time = ?, benchmark = ?, data_adapter = ?,
                new_stock_exclusion_days = ?
            WHERE id = 1
            """,
            (
                settings.auto_update_time,
                settings.benchmark,
                settings.data_adapter,
                settings.new_stock_exclusion_days,
            ),
        )
    return _response(settings)
