from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import BaseModel, Field, model_validator

from fourseasquant.core_strategy import (
    CandidatePipelineDecision,
    FundamentalInvestigationTarget,
)
from fourseasquant.core_strategy_execution import (
    EntryPlanningDecision,
    MarketState,
)
from fourseasquant.core_strategy_positions import (
    HoldingPosition,
    PositionManagementDecision,
)


class StrategyFundamentalTargetSnapshot(BaseModel):
    """策略初筛后发布给基本面调查模块的不可变目标快照。"""

    actual_date: date
    strategy_version: str
    targets: list[FundamentalInvestigationTarget]
    published_at: datetime


class CoreStrategyInputVersions(BaseModel):
    market_environment: str = Field(min_length=1)
    technical_scores: str | None = Field(default=None, min_length=1)
    industry_chain: str | None = Field(default=None, min_length=1)
    fundamental: str | None = Field(default=None, min_length=1)
    public_opinion: str | None = Field(default=None, min_length=1)


class CoreStrategyDaySnapshot(BaseModel):
    """可回放的完整策略日结果，不包含任何上游模拟事实。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    market_state: MarketState
    input_versions: CoreStrategyInputVersions
    candidate_pipeline: CandidatePipelineDecision
    entry_planning: EntryPlanningDecision
    position_management: PositionManagementDecision

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        decisions = (
            self.candidate_pipeline,
            self.entry_planning,
            self.position_management,
        )
        if any(
            decision.actual_date != self.actual_date
            for decision in decisions
        ):
            raise ValueError(
                "策略日快照的所有决定必须使用同一实际数据日期"
            )
        if any(
            decision.strategy_version != self.strategy_version
            for decision in decisions
        ):
            raise ValueError(
                "策略日快照的所有决定必须使用同一策略版本"
            )
        if self.position_management.publication_blocked:
            raise ValueError("公司行为不完整时不能发布策略日快照")
        pipeline_codes = {
            candidate.code
            for candidate in self.candidate_pipeline.candidates
        }
        entry_codes = {
            candidate.code
            for candidate in self.entry_planning.candidates
        }
        if not entry_codes.issubset(pipeline_codes):
            raise ValueError("新开仓候选必须来自同日初筛候选")
        return self


class PublishedCoreStrategyDaySnapshot(BaseModel):
    snapshot: CoreStrategyDaySnapshot
    published_at: datetime


PortfolioMarkSource = Literal[
    "daily_close",
    "entry_execution",
]


class PortfolioPositionMark(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    price: float = Field(gt=0)
    mark_date: date
    source: PortfolioMarkSource


class CoreStrategyPortfolioSnapshot(BaseModel):
    """可直接作为下一交易日输入的完整模拟组合状态。"""

    actual_date: date
    strategy_version: str = Field(min_length=1)
    initial_capital: float = Field(gt=0)
    available_cash: float = Field(ge=0)
    net_asset_value: float = Field(gt=0)
    positions: list[HoldingPosition]
    marks: list[PortfolioPositionMark]

    @model_validator(mode="after")
    def validate_complete_valuation(self) -> Self:
        position_codes = [position.code for position in self.positions]
        mark_codes = [mark.code for mark in self.marks]
        if len(set(position_codes)) != len(position_codes):
            raise ValueError("组合持仓股票代码不能重复")
        if len(set(mark_codes)) != len(mark_codes):
            raise ValueError("组合收盘标记股票代码不能重复")
        if set(position_codes) != set(mark_codes):
            raise ValueError("组合每只持仓都必须且只能有一个价格标记")
        if any(mark.mark_date != self.actual_date for mark in self.marks):
            raise ValueError("组合价格标记必须来自策略实际日期")
        maximum_positions = min(
            5,
            max(1, int(self.initial_capital // 50_000) + 1),
        )
        if len(self.positions) > maximum_positions:
            raise ValueError("组合持仓数量超过初始资金对应上限")
        mark_by_code = {mark.code: mark.price for mark in self.marks}
        calculated_value = self.available_cash + sum(
            position.shares * mark_by_code[position.code]
            for position in self.positions
        )
        if abs(calculated_value - self.net_asset_value) > 0.01:
            raise ValueError("组合净值必须等于现金加全部持仓市值")
        return self


class PublishedCoreStrategyPortfolioSnapshot(BaseModel):
    snapshot: CoreStrategyPortfolioSnapshot
    published_at: datetime


def create_core_strategy_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_fundamental_target_snapshots (
            actual_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            targets_json TEXT NOT NULL CHECK(json_valid(targets_json)),
            target_count INTEGER NOT NULL CHECK(target_count >= 0),
            content_sha256 TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategy_fundamental_targets_latest
        ON strategy_fundamental_target_snapshots (
            actual_date DESC,
            published_at DESC,
            strategy_version DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_day_snapshots (
            actual_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            content_sha256 TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_core_strategy_day_latest
        ON core_strategy_day_snapshots (
            actual_date DESC,
            published_at DESC,
            strategy_version DESC
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS core_strategy_portfolio_snapshots (
            actual_date TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
            content_sha256 TEXT NOT NULL,
            published_at TEXT NOT NULL,
            PRIMARY KEY (actual_date, strategy_version)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_core_strategy_portfolio_latest
        ON core_strategy_portfolio_snapshots (
            actual_date DESC,
            published_at DESC,
            strategy_version DESC
        )
        """
    )


def publish_core_strategy_day(
    path: Path,
    *,
    snapshot: CoreStrategyDaySnapshot,
    published_at: datetime,
) -> PublishedCoreStrategyDaySnapshot:
    """原子发布不可变的完整策略日快照。"""

    if published_at.tzinfo is None:
        raise ValueError("策略日快照发布时间必须包含时区")
    payload_json = snapshot.model_dump_json()
    content_sha256 = hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        existing = connection.execute(
            """
            SELECT payload_json, content_sha256, published_at
            FROM core_strategy_day_snapshots
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (
                snapshot.actual_date.isoformat(),
                snapshot.strategy_version,
            ),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != content_sha256:
                raise ValueError(
                    "同日同策略版本的策略快照已发布；"
                    "修改结果必须使用新策略版本"
                )
            return PublishedCoreStrategyDaySnapshot(
                snapshot=CoreStrategyDaySnapshot.model_validate_json(
                    cast(str, existing[0])
                ),
                published_at=datetime.fromisoformat(cast(str, existing[2])),
            )
        connection.execute(
            """
            INSERT INTO core_strategy_day_snapshots (
                actual_date,
                strategy_version,
                payload_json,
                content_sha256,
                published_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.actual_date.isoformat(),
                snapshot.strategy_version,
                payload_json,
                content_sha256,
                published_at.isoformat(),
            ),
        )
    return PublishedCoreStrategyDaySnapshot(
        snapshot=snapshot,
        published_at=published_at,
    )


def read_core_strategy_day(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str | None = None,
) -> PublishedCoreStrategyDaySnapshot | None:
    """
    读取指定日期策略结果；
    未指定版本时返回该日最后发布版本。
    """

    if not path.exists():
        return None
    filters = ["actual_date = ?"]
    parameters: list[object] = [actual_date.isoformat()]
    if strategy_version is not None:
        normalized_version = strategy_version.strip()
        if not normalized_version:
            raise ValueError("策略版本不能为空")
        filters.append("strategy_version = ?")
        parameters.append(normalized_version)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            f"""
            SELECT payload_json, published_at
            FROM core_strategy_day_snapshots
            WHERE {' AND '.join(filters)}
            ORDER BY published_at DESC, strategy_version DESC
            LIMIT 1
            """,
            tuple(parameters),
        ).fetchone()
    if row is None:
        return None
    return PublishedCoreStrategyDaySnapshot(
        snapshot=CoreStrategyDaySnapshot.model_validate_json(
            cast(str, row[0])
        ),
        published_at=datetime.fromisoformat(cast(str, row[1])),
    )


def read_latest_core_strategy_day(
    path: Path,
    *,
    as_of_date: date,
) -> PublishedCoreStrategyDaySnapshot | None:
    """读取不晚于指定日期的最近完整策略结果。"""

    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            """
            SELECT payload_json, published_at
            FROM core_strategy_day_snapshots
            WHERE actual_date <= ?
            ORDER BY actual_date DESC, published_at DESC,
                     strategy_version DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return PublishedCoreStrategyDaySnapshot(
        snapshot=CoreStrategyDaySnapshot.model_validate_json(
            cast(str, row[0])
        ),
        published_at=datetime.fromisoformat(cast(str, row[1])),
    )


def publish_core_strategy_portfolio(
    path: Path,
    *,
    snapshot: CoreStrategyPortfolioSnapshot,
    published_at: datetime,
) -> PublishedCoreStrategyPortfolioSnapshot:
    """
    发布完整且不可变的组合状态。

    对应策略日必须已经发布；任何持仓缺少价格标记时，模型校验会在
    写入前失败，因此残缺组合不会覆盖上一版有效状态。
    """

    if published_at.tzinfo is None:
        raise ValueError("组合状态发布时间必须包含时区")
    payload_json = snapshot.model_dump_json()
    content_sha256 = hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        strategy_day_exists = connection.execute(
            """
            SELECT 1
            FROM core_strategy_day_snapshots
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (
                snapshot.actual_date.isoformat(),
                snapshot.strategy_version,
            ),
        ).fetchone()
        if strategy_day_exists is None:
            raise ValueError("组合状态必须引用已经发布的同日策略结果")
        existing = connection.execute(
            """
            SELECT payload_json, content_sha256, published_at
            FROM core_strategy_portfolio_snapshots
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (
                snapshot.actual_date.isoformat(),
                snapshot.strategy_version,
            ),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != content_sha256:
                raise ValueError(
                    "同日同策略版本的组合状态已发布；"
                    "修改结果必须使用新策略版本"
                )
            return PublishedCoreStrategyPortfolioSnapshot(
                snapshot=CoreStrategyPortfolioSnapshot.model_validate_json(
                    cast(str, existing[0])
                ),
                published_at=datetime.fromisoformat(cast(str, existing[2])),
            )
        connection.execute(
            """
            INSERT INTO core_strategy_portfolio_snapshots (
                actual_date,
                strategy_version,
                payload_json,
                content_sha256,
                published_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.actual_date.isoformat(),
                snapshot.strategy_version,
                payload_json,
                content_sha256,
                published_at.isoformat(),
            ),
        )
    return PublishedCoreStrategyPortfolioSnapshot(
        snapshot=snapshot,
        published_at=published_at,
    )


def read_core_strategy_portfolio(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str | None = None,
) -> PublishedCoreStrategyPortfolioSnapshot | None:
    """读取指定日期的组合状态；未指定版本时取最后发布版本。"""

    if not path.exists():
        return None
    filters = ["actual_date = ?"]
    parameters: list[object] = [actual_date.isoformat()]
    if strategy_version is not None:
        normalized_version = strategy_version.strip()
        if not normalized_version:
            raise ValueError("策略版本不能为空")
        filters.append("strategy_version = ?")
        parameters.append(normalized_version)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            f"""
            SELECT payload_json, published_at
            FROM core_strategy_portfolio_snapshots
            WHERE {' AND '.join(filters)}
            ORDER BY published_at DESC, strategy_version DESC
            LIMIT 1
            """,
            tuple(parameters),
        ).fetchone()
    if row is None:
        return None
    return PublishedCoreStrategyPortfolioSnapshot(
        snapshot=CoreStrategyPortfolioSnapshot.model_validate_json(
            cast(str, row[0])
        ),
        published_at=datetime.fromisoformat(cast(str, row[1])),
    )


def read_latest_core_strategy_portfolio(
    path: Path,
    *,
    as_of_date: date,
) -> PublishedCoreStrategyPortfolioSnapshot | None:
    """读取不晚于指定日期的最近完整组合状态。"""

    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            """
            SELECT payload_json, published_at
            FROM core_strategy_portfolio_snapshots
            WHERE actual_date <= ?
            ORDER BY actual_date DESC, published_at DESC,
                     strategy_version DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return PublishedCoreStrategyPortfolioSnapshot(
        snapshot=CoreStrategyPortfolioSnapshot.model_validate_json(
            cast(str, row[0])
        ),
        published_at=datetime.fromisoformat(cast(str, row[1])),
    )


def publish_strategy_fundamental_targets(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str,
    targets: list[FundamentalInvestigationTarget],
    published_at: datetime,
) -> StrategyFundamentalTargetSnapshot:
    """
    发布有序基本面调查目标。

    同一交易日和策略版本只能对应一个内容哈希。
    调用方需要修改目标时，
    必须提升策略版本，不能原地覆盖已经被调查模块读取的目标。
    """

    normalized_version = strategy_version.strip()
    if not normalized_version:
        raise ValueError("策略版本不能为空")
    if published_at.tzinfo is None:
        raise ValueError("基本面调查目标发布时间必须包含时区")
    codes = [target.code for target in targets]
    if len(set(codes)) != len(codes):
        raise ValueError("基本面调查目标不能包含重复股票代码")
    for target in targets:
        if target.actual_date != actual_date:
            raise ValueError(
                "基本面调查目标日期必须与目标快照日期一致"
            )
        if target.strategy_version != normalized_version:
            raise ValueError(
                "基本面调查目标策略版本必须与目标快照版本一致"
            )

    payload = [
        target.model_dump(mode="json")
        for target in targets
    ]
    targets_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_sha256 = hashlib.sha256(targets_json.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        existing = connection.execute(
            """
            SELECT targets_json, content_sha256, published_at
            FROM strategy_fundamental_target_snapshots
            WHERE actual_date = ? AND strategy_version = ?
            """,
            (actual_date.isoformat(), normalized_version),
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != content_sha256:
                raise ValueError(
                    "同日同策略版本的基本面调查目标已发布；"
                    "修改目标必须使用新策略版本"
                )
            return StrategyFundamentalTargetSnapshot(
                actual_date=actual_date,
                strategy_version=normalized_version,
                targets=_targets_from_json(cast(str, existing[0])),
                published_at=datetime.fromisoformat(cast(str, existing[2])),
            )
        connection.execute(
            """
            INSERT INTO strategy_fundamental_target_snapshots (
                actual_date,
                strategy_version,
                targets_json,
                target_count,
                content_sha256,
                published_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actual_date.isoformat(),
                normalized_version,
                targets_json,
                len(targets),
                content_sha256,
                published_at.isoformat(),
            ),
        )
    return StrategyFundamentalTargetSnapshot(
        actual_date=actual_date,
        strategy_version=normalized_version,
        targets=list(targets),
        published_at=published_at,
    )


def read_strategy_fundamental_targets(
    path: Path,
    *,
    actual_date: date,
    strategy_version: str | None = None,
) -> StrategyFundamentalTargetSnapshot | None:
    """
    读取指定日期的目标；
    未指定版本时取该日最后发布的版本。
    """

    if not path.exists():
        return None
    filters = ["actual_date = ?"]
    parameters: list[object] = [actual_date.isoformat()]
    if strategy_version is not None:
        normalized_version = strategy_version.strip()
        if not normalized_version:
            raise ValueError("策略版本不能为空")
        filters.append("strategy_version = ?")
        parameters.append(normalized_version)
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            f"""
            SELECT actual_date, strategy_version, targets_json, published_at
            FROM strategy_fundamental_target_snapshots
            WHERE {' AND '.join(filters)}
            ORDER BY published_at DESC, strategy_version DESC
            LIMIT 1
            """,
            tuple(parameters),
        ).fetchone()
    if row is None:
        return None
    return StrategyFundamentalTargetSnapshot(
        actual_date=date.fromisoformat(cast(str, row[0])),
        strategy_version=cast(str, row[1]),
        targets=_targets_from_json(cast(str, row[2])),
        published_at=datetime.fromisoformat(cast(str, row[3])),
    )


def read_latest_strategy_fundamental_targets(
    path: Path,
    *,
    as_of_date: date,
) -> StrategyFundamentalTargetSnapshot | None:
    """读取不晚于指定日期的最近一版调查目标。"""

    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        create_core_strategy_tables(connection)
        row = connection.execute(
            """
            SELECT actual_date, strategy_version, targets_json, published_at
            FROM strategy_fundamental_target_snapshots
            WHERE actual_date <= ?
            ORDER BY actual_date DESC, published_at DESC, strategy_version DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return StrategyFundamentalTargetSnapshot(
        actual_date=date.fromisoformat(cast(str, row[0])),
        strategy_version=cast(str, row[1]),
        targets=_targets_from_json(cast(str, row[2])),
        published_at=datetime.fromisoformat(cast(str, row[3])),
    )


def _targets_from_json(value: str) -> list[FundamentalInvestigationTarget]:
    payload = cast(list[object], json.loads(value))
    return [
        FundamentalInvestigationTarget.model_validate(item)
        for item in payload
    ]
