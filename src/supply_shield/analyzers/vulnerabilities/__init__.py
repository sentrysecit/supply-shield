"""Consulta de vulnerabilidades desde OSV.dev."""

from supply_shield.analyzers.vulnerabilities.analyzer import VulnerabilityAnalyzer
from supply_shield.analyzers.vulnerabilities.client import OsvAdvisory, OsvClient, OsvError
from supply_shield.analyzers.vulnerabilities.severity import (
    score_to_severity,
    severity_from_advisory,
)

__all__ = [
    "OsvAdvisory",
    "OsvClient",
    "OsvError",
    "VulnerabilityAnalyzer",
    "score_to_severity",
    "severity_from_advisory",
]
