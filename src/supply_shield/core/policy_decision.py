"""Decisión del firewall para una versión de paquete."""

from pydantic import BaseModel

from supply_shield.core.enums import DecisionAction
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN


class PolicyDecision(BaseModel):
    """Decisión del firewall para una versión de paquete."""

    model_config = FROZEN

    action: DecisionAction
    reasons: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    degraded: bool = False
