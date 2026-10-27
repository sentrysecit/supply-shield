"""Conjuntos cerrados de la identidad del paquete, los hallazgos y las decisiones."""

from enum import StrEnum


class Ecosystem(StrEnum):
    """Registro al que pertenece el nombre de un paquete."""

    NPM = "npm"
    PYPI = "pypi"


class Severity(StrEnum):
    """Gravedad de un hallazgo. ``UNKNOWN`` indica que la fuente no dio una calificación usable."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class DecisionAction(StrEnum):
    """Acción del proxy ante una versión de paquete.

    Una política de solo registro sigue dando ``ALLOW``: el paquete se sirve y los
    hallazgos quedan en la decisión para que la auditoría los conserve.
    """

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
