"""产业链供需候选模块。"""

from fourseasquant.industry_chain.schema import create_industry_chain_core_tables
from fourseasquant.industry_chain.company_evidence import (
    LocalCompanyEvidence,
    StockScopeViolation,
)
from fourseasquant.industry_chain.local_data import (
    LocalIndustryChainData,
    UnknownUniverseVersion,
)
from fourseasquant.industry_chain.repository import (
    AppendEvidenceResult,
    IneligibleEvidenceError,
    ImmutableRecordConflict,
    IndustryChainRepository,
    MissingEvidenceError,
    PublishSelectionResult,
    UnknownStockUniverseError,
)
from fourseasquant.industry_chain.validation import (
    FutureEvidenceError,
    IndustryChainValidationError,
)
from fourseasquant.industry_chain.universe import (
    EligibleSecurity,
    EligibleUniverseSnapshot,
    EligibleUniverseUnavailable,
)

__all__ = [
    "AppendEvidenceResult",
    "FutureEvidenceError",
    "EligibleSecurity",
    "EligibleUniverseSnapshot",
    "EligibleUniverseUnavailable",
    "IneligibleEvidenceError",
    "ImmutableRecordConflict",
    "IndustryChainRepository",
    "IndustryChainValidationError",
    "LocalCompanyEvidence",
    "LocalIndustryChainData",
    "MissingEvidenceError",
    "PublishSelectionResult",
    "StockScopeViolation",
    "UnknownUniverseVersion",
    "UnknownStockUniverseError",
    "create_industry_chain_core_tables",
]
