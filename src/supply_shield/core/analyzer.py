"""Protocolo que implementa cada detector de paquetes."""

from typing import Protocol

from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext


class Analyzer(Protocol):
    """Detector que revisa un paquete y devuelve hallazgos.

    Un paquete limpio es una lista vacía, no una excepción. Si el detector no
    puede alcanzar su fuente, lanza un error controlado para que el evaluador
    aplique el modo de fallo de esa regla, en lugar de tratar el silencio como
    ausencia de problemas.
    """

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve los hallazgos de ``package``. Una lista vacía no reporta nada."""
        ...
