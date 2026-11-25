"""Traduce un aviso de OSV a una severidad, según el ADR 0003.

CVSS v3.1 gana sobre v3.0, y cualquier vector v3 legible gana sobre v4. Un
vector v4 se usa solo cuando el aviso no trae vector v3. Si ningún vector se
puede puntuar, se usa la severidad cualitativa del aviso de GitHub (GHSA).
``MODERATE`` se guarda como ``medium``. Lo que siga faltando es ``UNKNOWN``,
que no es una severidad baja.
"""

from collections.abc import Callable, Sequence

from cvss import CVSS3, CVSS4
from cvss.exceptions import CVSSError

from supply_shield.core.enums import Severity

_CRITICAL_AT = 9.0
_HIGH_AT = 7.0
_MEDIUM_AT = 4.0
_LOW_AT = 0.1

_GHSA_SEVERITY: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "moderate": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}


def score_to_severity(score: float) -> Severity:
    """Banda cualitativa de CVSS v3 para un puntaje base. ``0.0`` es ``UNKNOWN``."""
    if score >= _CRITICAL_AT:
        return Severity.CRITICAL
    if score >= _HIGH_AT:
        return Severity.HIGH
    if score >= _MEDIUM_AT:
        return Severity.MEDIUM
    if score >= _LOW_AT:
        return Severity.LOW
    return Severity.UNKNOWN


def severity_from_advisory(
    vectors: Sequence[tuple[str, str]],
    ecosystem_severity: str | None,
) -> Severity:
    """Severidad de un aviso.

    ``vectors`` es ``(tipo, cadena de puntaje)`` como lo publica OSV, por ejemplo
    ``("CVSS_V3", "CVSS:3.1/AV:N/...")``. Un vector v3 presente pero ilegible no
    pasa a v4: v4 aplica solo cuando no hay vector v3. Un vector ilegible sin
    severidad de ecosistema queda en ``UNKNOWN``.
    """
    v31 = _vectors_with_prefix(vectors, "CVSS:3.1/")
    v30 = _vectors_with_prefix(vectors, "CVSS:3.0/")
    if v31 or v30:
        score = _best_score(v31, _cvss3_score)
        if score is None:
            score = _best_score(v30, _cvss3_score)
        if score is not None:
            return score_to_severity(score)
        return _from_ecosystem(ecosystem_severity)

    v4 = _vectors_with_prefix(vectors, "CVSS:4.0/")
    score = _best_score(v4, _cvss4_score)
    if score is not None:
        return score_to_severity(score)
    return _from_ecosystem(ecosystem_severity)


def _vectors_with_prefix(vectors: Sequence[tuple[str, str]], prefix: str) -> list[str]:
    return [score for _, score in vectors if score.startswith(prefix)]


def _best_score(vectors: list[str], score_of: Callable[[str], float | None]) -> float | None:
    best: float | None = None
    for vector in vectors:
        raw = score_of(vector)
        if raw is None:
            continue
        best = raw if best is None else max(best, raw)
    return best


def _cvss3_score(vector: str) -> float | None:
    return _library_score(lambda: CVSS3(vector).scores()[0])


def _cvss4_score(vector: str) -> float | None:
    return _library_score(lambda: CVSS4(vector).scores()[0])


def _library_score(read: Callable[[], object]) -> float | None:
    try:
        raw = read()
    except CVSSError:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _from_ecosystem(label: str | None) -> Severity:
    if label is None:
        return Severity.UNKNOWN
    return _GHSA_SEVERITY.get(label.strip().casefold(), Severity.UNKNOWN)
