"""Problema reportado sobre un paquete en revisión."""

from pydantic import BaseModel, Field

from supply_shield.core.enums import Severity
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef


class Finding(BaseModel):
    """Problema que un detector reporta sobre un paquete en revisión.

    ``advisory_ids`` lleva los identificadores de vulnerabilidad (CVE, GHSA o un
    id ``MAL-`` de OSV) para descartar decisiones cacheadas cuando el aviso
    cambia. ``fixed_in`` lista las versiones que, según la fuente, ya incluyen
    la corrección.
    """

    model_config = FROZEN

    rule: str = Field(min_length=1)
    severity: Severity
    source: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    package: PackageRef
    references: tuple[str, ...] = ()
    advisory_ids: tuple[str, ...] = ()
    fixed_in: tuple[str, ...] = ()
