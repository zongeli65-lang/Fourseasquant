from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fourseasquant.industry_chain.candidate_expansion import (
    expand_candidate_companies,
)
from fourseasquant.industry_chain.company_evidence import LocalCompanyEvidence
from fourseasquant.industry_chain.universe import (
    EligibleSecurity,
    EligibleUniverseSnapshot,
)


BEIJING = ZoneInfo("Asia/Shanghai")


class _LocalDataStub:
    def __init__(self, companies: dict[str, LocalCompanyEvidence]) -> None:
        self._companies = companies

    def company_evidence(
        self,
        *,
        universe_version: str,
        codes: list[str],
        as_of_time: datetime,
    ) -> tuple[LocalCompanyEvidence, ...]:
        del universe_version, as_of_time
        return tuple(self._companies[code] for code in codes)

    def recall_company_evidence(
        self,
        *,
        universe_version: str,
        terms: list[str],
        corpus: list[str],
        as_of_time: datetime,
        limit: int,
    ) -> tuple[LocalCompanyEvidence, ...]:
        del universe_version, terms, corpus, as_of_time, limit
        return ()


def test_candidate_expansion_does_not_stop_at_eight_companies() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    securities = tuple(
        EligibleSecurity(
            code=f"000{index:03d}",
            name=f"候选公司{index:02d}",
            listing_trading_days=300,
        )
        for index in range(1, 13)
    )
    universe = EligibleUniverseSnapshot(
        universe_version="test-universe",
        available_as_of_time=now,
        actual_data_date=date(2026, 7, 26),
        source="test",
        source_published_at=now,
        eligibility_rules_version="test",
        minimum_listing_trading_days=60,
        securities=securities,
    )
    companies = {
        security.code: LocalCompanyEvidence(
            code=security.code,
            name=security.name,
            universe_version=universe.universe_version,
            availability="unavailable",
            evidence_as_of_date=None,
            published_at=None,
            rules_version="test",
            main_business_name=None,
            gross_profit_share=None,
            revenue_share=None,
            source_urls=(),
            ownership_evidence_available=False,
            limitations=("待调查",),
        )
        for security in securities
    }

    result = expand_candidate_companies(
        local_data=_LocalDataStub(companies),  # type: ignore[arg-type]
        universe=universe,
        original_corpus=[],
        expanded_corpus=[
            " ".join(security.name for security in securities[:10])
        ],
        recall_terms=["测试产品"],
        as_of_time=now,
        limit=24,
    )

    assert len(result.companies) == 10
    assert result.directly_mentioned_codes == tuple(
        security.code for security in securities[:10]
    )


def test_candidate_expansion_does_not_match_short_name_inside_other_entity() -> None:
    now = datetime(2026, 7, 27, 12, 0, tzinfo=BEIJING)
    security = EligibleSecurity(
        code="601099",
        name="太平洋",
        listing_trading_days=300,
    )
    universe = EligibleUniverseSnapshot(
        universe_version="test-universe",
        available_as_of_time=now,
        actual_data_date=date(2026, 7, 26),
        source="test",
        source_published_at=now,
        eligibility_rules_version="test",
        minimum_listing_trading_days=60,
        securities=(security,),
    )
    company = LocalCompanyEvidence(
        code=security.code,
        name=security.name,
        universe_version=universe.universe_version,
        availability="unavailable",
        evidence_as_of_date=None,
        published_at=None,
        rules_version="test",
        main_business_name=None,
        gross_profit_share=None,
        revenue_share=None,
        source_urls=(),
        ownership_evidence_available=False,
        limitations=("待调查",),
    )

    result = expand_candidate_companies(
        local_data=_LocalDataStub({security.code: company}),  # type: ignore[arg-type]
        universe=universe,
        original_corpus=[],
        expanded_corpus=["南通中集太平洋海洋工程有限公司获得船舶订单"],
        recall_terms=["LNG加注船"],
        as_of_time=now,
        limit=24,
    )

    assert result.directly_mentioned_codes == ()
    assert result.companies == ()
