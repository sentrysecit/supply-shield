"""Contratos compartidos por el proxy, los detectores y el motor de política."""

from supply_shield.core.analyzer import Analyzer
from supply_shield.core.enums import DecisionAction, Ecosystem, Severity
from supply_shield.core.finding import Finding
from supply_shield.core.maintainer import Maintainer
from supply_shield.core.naming import normalize_package_name
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext
from supply_shield.core.upstream_metadata import UpstreamMetadata

__all__ = [
    "Analyzer",
    "DecisionAction",
    "Ecosystem",
    "Finding",
    "Maintainer",
    "PackageRef",
    "PackageRelease",
    "PolicyDecision",
    "RequestContext",
    "Severity",
    "UpstreamMetadata",
    "normalize_package_name",
]
