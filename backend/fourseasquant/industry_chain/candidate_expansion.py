from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .company_evidence import LocalCompanyEvidence
from .entity_matching import (
    company_name_mention_count,
    company_name_mentioned,
)
from .local_data import LocalIndustryChainData
from .universe import EligibleUniverseSnapshot


@dataclass(frozen=True)
class CandidateExpansionResult:
    """一次事件的公司调查范围，不代表任何公司已经入选。"""

    companies: tuple[LocalCompanyEvidence, ...]
    directly_mentioned_codes: tuple[str, ...]
    business_matched_codes: tuple[str, ...]


def expand_candidate_companies(
    *,
    local_data: LocalIndustryChainData,
    universe: EligibleUniverseSnapshot,
    original_corpus: list[str],
    expanded_corpus: list[str],
    recall_terms: list[str],
    as_of_time: datetime,
    limit: int,
) -> CandidateExpansionResult:
    """合并原始点名、扩展搜索点名和本地主营匹配，形成调查集合。"""
    if limit < 1:
        raise ValueError("候选调查上限必须大于零")
    original_codes = _mentioned_codes(
        universe,
        corpus=original_corpus,
        limit=limit,
    )
    expanded_codes = _mentioned_codes(
        universe,
        corpus=expanded_corpus,
        limit=limit,
    )
    mentioned_codes = tuple(
        dict.fromkeys((*original_codes, *expanded_codes))
    )[:limit]
    mentioned = (
        local_data.company_evidence(
            universe_version=universe.universe_version,
            codes=list(mentioned_codes),
            as_of_time=as_of_time,
        )
        if mentioned_codes
        else ()
    )
    business_matched = local_data.recall_company_evidence(
        universe_version=universe.universe_version,
        terms=recall_terms,
        corpus=[*original_corpus, *expanded_corpus],
        as_of_time=as_of_time,
        limit=limit,
    )
    companies = _deduplicate_companies(
        (*mentioned, *business_matched),
        limit=limit,
    )
    return CandidateExpansionResult(
        companies=companies,
        directly_mentioned_codes=mentioned_codes,
        business_matched_codes=tuple(
            company.code for company in business_matched
        ),
    )


def _mentioned_codes(
    universe: EligibleUniverseSnapshot,
    *,
    corpus: list[str],
    limit: int,
) -> tuple[str, ...]:
    text = " ".join(corpus)
    matches = [
        item
        for item in universe.securities
        if company_name_mentioned(item.name, text)
    ]
    matches.sort(
        key=lambda item: (
            -company_name_mention_count(item.name, text),
            -len(item.name),
            item.code,
        )
    )
    return tuple(item.code for item in matches[:limit])


def _deduplicate_companies(
    companies: tuple[LocalCompanyEvidence, ...],
    *,
    limit: int,
) -> tuple[LocalCompanyEvidence, ...]:
    by_code: dict[str, LocalCompanyEvidence] = {}
    for company in companies:
        by_code.setdefault(company.code, company)
    return tuple(by_code.values())[:limit]
