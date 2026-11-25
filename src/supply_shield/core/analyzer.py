"""Protocolo que implementa cada detector de paquetes."""

from collections.abc import Sequence
from typing import Protocol

from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext


class IncompleteAnalysisError(Exception):
    """El detector produjo hallazgos, pero no pudo terminar la consulta.

    Quien llama conserva ``findings`` y marca la decisión como degradada. Una
    lista vacía aquí no significa que el paquete esté limpio: significa que la
    fuente falló antes de confirmar un aviso.
    """

    def __init__(self, findings: Sequence[Finding], message: str) -> None:
        super().__init__(message)
        self.findings = tuple(findings)


class Analyzer(Protocol):
    """Detector que revisa un paquete y devuelve hallazgos.

    Un paquete limpio es una lista vacía, no una excepción. Si el detector no
    puede alcanzar su fuente, lanza un error controlado para que el evaluador
    aplique el modo de fallo de esa regla, en lugar de tratar el silencio como
    ausencia de problemas. Si alcanzó una parte y el resto falló, lanza
    :class:`IncompleteAnalysisError` con los hallazgos que sí confirmó.
    """

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve los hallazgos de ``package``. Una lista vacía no reporta nada."""
        ...
