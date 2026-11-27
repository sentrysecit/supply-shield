"""Protocolo que implementa cada detector de paquetes."""

from collections.abc import Sequence
from typing import Protocol

from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext


class IncompleteAnalysisError(Exception):
    """El detector produjo hallazgos, pero no pudo terminar la consulta.

    Quien llama conserva ``findings``, marca la decisión como degradada y aplica
    el modo de falla de la regla. Una lista vacía aquí no significa que el
    paquete esté limpio: significa que la fuente falló antes de confirmar un aviso.
    """

    def __init__(self, findings: Sequence[Finding], message: str) -> None:
        super().__init__(message)
        self.findings = tuple(findings)


class Analyzer(Protocol):
    """Detector que revisa un paquete y devuelve hallazgos.

    ``rule`` es el id de ``supply_shield.policy.Rule`` que cubre. Si el detector
    no puede alcanzar su fuente, lanza un error controlado para que quien llama
    aplique ``on_failure`` de esa regla, en lugar de tratar el silencio como
    ausencia de problemas. Si alcanzó una parte y el resto falló, lanza
    :class:`IncompleteAnalysisError` con los hallazgos que sí confirmó. Un
    paquete limpio es una lista vacía, no una excepción.
    """

    rule: str

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve los hallazgos de ``package``. Una lista vacía no reporta nada."""
        ...
