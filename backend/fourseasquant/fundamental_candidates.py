from __future__ import annotations

import hashlib
import random
from datetime import date

import pandas as pd
from pydantic import BaseModel


PRIMARY_LIMIT = 140
BOARD_SAMPLE_LIMIT = 40
RANDOM_SAMPLE_LIMIT = 20


class FundamentalCandidatePool(BaseModel):
    as_of_date: date
    primary_codes: list[str]
    board_sample_codes: list[str]
    random_sample_codes: list[str]
    codes: list[str]


def codes_announced_on_or_before(
    frame: pd.DataFrame,
    *,
    code_column: str,
    date_column: str,
    as_of_date: date,
) -> list[str]:
    if code_column not in frame or date_column not in frame:
        return []
    announced_at = pd.to_datetime(frame[date_column], errors="coerce")
    eligible = frame.loc[
        announced_at.notna() & (announced_at.dt.date <= as_of_date),
        code_column,
    ]
    return eligible.astype(str).tolist()


def build_fundamental_candidate_pool(
    *,
    as_of_date: date,
    heat_codes: list[str],
    technical_codes: list[str],
    announcement_codes: list[str],
    board_members: dict[str, list[str]],
    market_codes: list[str],
) -> FundamentalCandidatePool:
    eligible = set(market_codes)
    primary_codes = _round_robin_unique(
        [
            _eligible_unique(announcement_codes, eligible),
            _eligible_unique(technical_codes, eligible),
            _eligible_unique(heat_codes, eligible),
        ],
        limit=PRIMARY_LIMIT,
        excluded=set(),
    )
    selected = set(primary_codes)
    board_sample_codes = _round_robin_unique(
        [
            _eligible_unique(board_members[name], eligible)
            for name in sorted(board_members)
        ],
        limit=BOARD_SAMPLE_LIMIT,
        excluded=selected,
    )
    selected.update(board_sample_codes)
    remaining = sorted(eligible.difference(selected))
    generator = random.Random(_date_seed(as_of_date))
    random_sample_codes = generator.sample(
        remaining,
        k=min(RANDOM_SAMPLE_LIMIT, len(remaining)),
    )
    codes = [
        *primary_codes,
        *board_sample_codes,
        *random_sample_codes,
    ]
    return FundamentalCandidatePool(
        as_of_date=as_of_date,
        primary_codes=primary_codes,
        board_sample_codes=board_sample_codes,
        random_sample_codes=random_sample_codes,
        codes=codes,
    )


def _eligible_unique(codes: list[str], eligible: set[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for code in codes:
        normalized = str(code).zfill(6)
        if normalized in eligible and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _round_robin_unique(
    groups: list[list[str]],
    *,
    limit: int,
    excluded: set[str],
) -> list[str]:
    result: list[str] = []
    seen = set(excluded)
    positions = [0 for _ in groups]
    while len(result) < limit:
        progressed = False
        for group_index, group in enumerate(groups):
            while (
                positions[group_index] < len(group)
                and group[positions[group_index]] in seen
            ):
                positions[group_index] += 1
            if positions[group_index] >= len(group):
                continue
            code = group[positions[group_index]]
            positions[group_index] += 1
            seen.add(code)
            result.append(code)
            progressed = True
            if len(result) == limit:
                break
        if not progressed:
            break
    return result


def _date_seed(value: date) -> int:
    digest = hashlib.sha256(value.isoformat().encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big")
