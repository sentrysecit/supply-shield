"""Detección de typosquatting por similitud de nombre con paquetes populares."""

from supply_shield.analyzers.typosquatting.allowlist import (
    AllowedName,
    AllowlistLoadError,
    TyposquattingAllowlist,
    load_allowlist,
    parse_allowlist,
)
from supply_shield.analyzers.typosquatting.analyzer import (
    SeverityThresholds,
    TyposquattingAnalyzer,
)
from supply_shield.analyzers.typosquatting.index import PopularIndex
from supply_shield.analyzers.typosquatting.popular import (
    PopularPackages,
    PopularPackagesSource,
    load_popular_packages,
)
from supply_shield.analyzers.typosquatting.scoring import (
    DEFAULT_WEIGHTS,
    Technique,
    TyposquatMatch,
    TyposquattingWeights,
    score_name,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "AllowedName",
    "AllowlistLoadError",
    "PopularIndex",
    "PopularPackages",
    "PopularPackagesSource",
    "SeverityThresholds",
    "Technique",
    "TyposquatMatch",
    "TyposquattingAllowlist",
    "TyposquattingAnalyzer",
    "TyposquattingWeights",
    "load_allowlist",
    "load_popular_packages",
    "parse_allowlist",
    "score_name",
]
