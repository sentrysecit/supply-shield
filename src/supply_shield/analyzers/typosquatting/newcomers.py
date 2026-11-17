"""Cuarentena de los nombres que entran a la lista de paquetes populares.

Un nombre de la lista popular nunca se marca como typosquatting, así que la lista
es un ancla de confianza: un typosquat que infle sus descargas hasta entrar al top
quedaría exento del detector. ``admit_newcomers`` revisa cada nombre que entra
contra la lista anterior y deja fuera al que imita a un paquete que ya era popular,
hasta que alguien lo apruebe en la lista de permitidos.

Es una función pura: no hace E/S ni guarda estado.
"""

from collections.abc import Iterable

from pydantic import BaseModel, Field

from supply_shield.analyzers.typosquatting.analyzer import SeverityThresholds
from supply_shield.analyzers.typosquatting.index import PopularIndex
from supply_shield.analyzers.typosquatting.scoring import TyposquatMatch, score_name
from supply_shield.core.enums import Severity
from supply_shield.core.model_config import FROZEN

# Severidades con las que la política por defecto bloquea la regla ``typosquatting``.
_HOLD_SEVERITIES = frozenset({Severity.HIGH, Severity.CRITICAL})


class NewcomerShortfallError(ValueError):
    """El ranking no alcanza para llenar la lista después de apartar la cuarentena."""


class HeldNewcomer(BaseModel):
    """Entrante que quedó fuera de la lista porque imita a un paquete que ya era popular."""

    model_config = FROZEN

    name: str
    source_rank: int = Field(ge=1)
    match: TyposquatMatch
    severity: Severity


class NewcomerReview(BaseModel):
    """Resultado de revisar un ranking nuevo contra la lista anterior.

    ``packages`` es la lista que se puede publicar. ``entered`` y ``left`` son los
    nombres que entran y salen respecto de la anterior; ``held``, los que quedaron
    en cuarentena.
    """

    model_config = FROZEN

    packages: tuple[str, ...]
    entered: tuple[str, ...]
    left: tuple[str, ...]
    held: tuple[HeldNewcomer, ...]


def admit_newcomers(
    ranked: Iterable[str],
    previous: PopularIndex | None,
    *,
    limit: int,
    allowed: frozenset[str] = frozenset(),
    thresholds: SeverityThresholds | None = None,
) -> NewcomerReview:
    """Arma la lista nueva con los primeros ``limit`` nombres de ``ranked`` que se admiten.

    ``ranked`` trae nombres normalizados y sin duplicados, del más popular al menos.
    Un nombre que ya estaba en ``previous`` se conserva. Uno nuevo se compara con
    ``previous``: si su parecido con un paquete que ya era popular alcanza para
    bloquear (``high`` o ``critical``), queda en cuarentena y su lugar lo toma el
    siguiente del ranking. Un nombre de ``allowed`` (la lista de permitidos) entra
    sin revisión. Sin ``previous`` (primera generación) se admite todo.

    Lanza ``NewcomerShortfallError`` si no quedan ``limit`` nombres admitidos.
    """
    thresholds = thresholds or SeverityThresholds()
    packages: list[str] = []
    held: list[HeldNewcomer] = []
    for source_rank, name in enumerate(ranked, start=1):
        if len(packages) == limit:
            break
        # ``score_name`` devuelve ``None`` para los nombres de ``previous`` y de ``allowed``.
        match = None if previous is None else score_name(name, previous, allowed=allowed)
        hold = _hold(name, source_rank, match, thresholds)
        if hold is None:
            packages.append(name)
        else:
            held.append(hold)
    if len(packages) < limit:
        msg = f"only {len(packages)} names admitted ({len(held)} held), expected {limit}"
        raise NewcomerShortfallError(msg)
    admitted = frozenset(packages)
    return NewcomerReview(
        packages=tuple(packages),
        entered=tuple(name for name in packages if previous is None or name not in previous),
        left=() if previous is None else tuple(n for n in previous.names if n not in admitted),
        held=tuple(held),
    )


def _hold(
    name: str, source_rank: int, match: TyposquatMatch | None, thresholds: SeverityThresholds
) -> HeldNewcomer | None:
    if match is None:
        return None
    severity = thresholds.severity_for(match.score)
    if severity not in _HOLD_SEVERITIES:
        return None
    return HeldNewcomer(name=name, source_rank=source_rank, match=match, severity=severity)
