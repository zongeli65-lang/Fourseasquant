from __future__ import annotations

from pydantic import BaseModel


class SectorMove(BaseModel):
    name: str
    change_pct: float


class SectorRanking(BaseModel):
    leaders: list[SectorMove]
    laggards: list[SectorMove]
    heatmap_moves: list[SectorMove]


class SectorPerformance(BaseModel):
    industries: SectorRanking
    concepts: SectorRanking


def build_sector_ranking(moves: list[SectorMove]) -> SectorRanking:
    descending = sorted(moves, key=lambda move: move.change_pct, reverse=True)
    ascending = list(reversed(descending))
    return SectorRanking(
        leaders=descending[:10],
        laggards=ascending[:10],
        heatmap_moves=descending,
    )
