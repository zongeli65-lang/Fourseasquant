from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field


BoardKind = Literal["industry", "concept"]


class BoardCandidateMember(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)


class BoardCandidate(BaseModel):
    board_id: str = Field(min_length=1)
    source_board_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: BoardKind
    members: list[BoardCandidateMember]


class BoardCandidateSnapshot(BaseModel):
    source: Literal["eastmoney"]
    effective_date: date
    complete: bool
    boards: list[BoardCandidate]
    errors: list[str]


def collect_eastmoney_board_candidate_snapshot(
    *,
    industry_catalog: pd.DataFrame,
    concept_catalog: pd.DataFrame,
    constituent_fetcher: Callable[[str], pd.DataFrame],
    effective_date: date,
    max_workers: int = 4,
) -> BoardCandidateSnapshot:
    catalog = [
        *_catalog_rows(industry_catalog, "industry"),
        *_catalog_rows(concept_catalog, "concept"),
    ]
    boards: list[BoardCandidate] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_board = {
            executor.submit(constituent_fetcher, board_code): (
                board_code,
                board_name,
                board_kind,
            )
            for board_code, board_name, board_kind in catalog
        }
        for future in as_completed(future_to_board):
            board_code, board_name, board_kind = future_to_board[future]
            try:
                members = _constituents(future.result())
            except Exception as error:
                errors.append(f"{board_code}: {error}")
                continue
            boards.append(
                BoardCandidate(
                    board_id=f"em:{board_kind}:{board_code}",
                    source_board_code=board_code,
                    name=board_name,
                    kind=board_kind,
                    members=members,
                )
            )
    boards.sort(key=lambda board: board.board_id)
    errors.sort()
    return BoardCandidateSnapshot(
        source="eastmoney",
        effective_date=effective_date,
        complete=not errors and len(boards) == len(catalog),
        boards=boards,
        errors=errors,
    )


def _catalog_rows(
    frame: pd.DataFrame,
    kind: BoardKind,
) -> list[tuple[str, str, BoardKind]]:
    required = {"板块名称", "板块代码"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"板块目录缺少字段: {sorted(missing)}")
    return [
        (str(code), str(name), kind)
        for name, code in frame.loc[:, ["板块名称", "板块代码"]].itertuples(
            index=False,
            name=None,
        )
    ]


def _constituents(frame: pd.DataFrame) -> list[BoardCandidateMember]:
    required = {"代码", "名称"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"板块成分缺少字段: {sorted(missing)}")
    return [
        BoardCandidateMember(code=str(code).zfill(6), name=str(name))
        for code, name in frame.loc[:, ["代码", "名称"]].itertuples(
            index=False,
            name=None,
        )
    ]
