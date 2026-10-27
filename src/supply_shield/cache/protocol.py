"""Contrato de caché del almacén en memoria y de un futuro backend en Redis."""

from enum import StrEnum
from typing import Protocol

from supply_shield.core.package_ref import PackageRef


class CacheKind(StrEnum):
    """Qué tiempo de vida usa un valor cacheado."""

    METADATA = "metadata"
    FINDINGS = "findings"
    DECISIONS = "decisions"


class Cache(Protocol):
    """Almacén de metadatos, hallazgos y decisiones.

    Las claves son cadenas opacas. Las etiquetas de paquete y de aviso permiten
    descartar todo lo afectado por un nombre de paquete o por un aviso, sin
    conocer cómo lo guarda el backend.
    """

    async def get(self, key: str) -> object | None:
        """Devuelve el valor cacheado, o ``None`` si falta o ya venció."""
        ...

    async def set(
        self,
        key: str,
        value: object,
        *,
        kind: CacheKind,
        package: PackageRef | None = None,
        advisory_ids: tuple[str, ...] = (),
    ) -> None:
        """Guarda ``value`` bajo ``key`` con el tiempo de vida de ``kind``."""
        ...

    async def invalidate_package(self, package: PackageRef) -> int:
        """Descarta lo etiquetado con este nombre de paquete y devuelve cuántas quitó."""
        ...

    async def invalidate_advisory(self, advisory_id: str) -> int:
        """Descarta lo etiquetado con este aviso y devuelve cuántas entradas quitó."""
        ...
