"""Datos del registro sobre un paquete, antes de aplicar ninguna política."""

from pydantic import BaseModel, Field

from supply_shield.core.maintainer import Maintainer
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.package_release import PackageRelease


class UpstreamMetadata(BaseModel):
    """Datos que el registro publicó sobre un paquete, antes de aplicar política."""

    model_config = FROZEN

    package: PackageRef
    versions: tuple[PackageRelease, ...]
    scripts: dict[str, str] = Field(default_factory=dict)
    maintainers: tuple[Maintainer, ...] = ()
