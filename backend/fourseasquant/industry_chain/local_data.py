from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fourseasquant.industry_chain.company_evidence import (
    LocalCompanyEvidence,
    SqliteCompanyEvidenceAdapter,
)
from fourseasquant.industry_chain.universe import (
    EligibleUniverseSnapshot,
    SqliteEligibleUniverseAdapter,
)


class UnknownUniverseVersion(LookupError):
    """调用方提交了未冻结的股票范围版本。"""


class LocalIndustryChainData:
    """模型可用的本地只读数据接口。"""

    def __init__(self, path: Path) -> None:
        self._universes = SqliteEligibleUniverseAdapter(path)
        self._companies = SqliteCompanyEvidenceAdapter(path)

    def eligible_universe(
        self,
        *,
        as_of_time: datetime,
    ) -> EligibleUniverseSnapshot:
        return self._universes.load(as_of_time=as_of_time)

    def company_evidence(
        self,
        *,
        universe_version: str,
        codes: list[str],
        as_of_time: datetime,
    ) -> tuple[LocalCompanyEvidence, ...]:
        universe = self._universes.read(universe_version)
        if universe is None:
            raise UnknownUniverseVersion(
                f"股票范围版本不存在：{universe_version}"
            )
        return self._companies.query(
            universe=universe,
            codes=codes,
            as_of_time=as_of_time,
        )

    def recall_company_evidence(
        self,
        *,
        universe_version: str,
        terms: list[str],
        corpus: list[str],
        as_of_time: datetime,
        limit: int,
    ) -> tuple[LocalCompanyEvidence, ...]:
        universe = self._universes.read(universe_version)
        if universe is None:
            raise UnknownUniverseVersion(
                f"股票范围版本不存在：{universe_version}"
            )
        return self._companies.recall(
            universe=universe,
            terms=terms,
            corpus=corpus,
            as_of_time=as_of_time,
            limit=limit,
        )
