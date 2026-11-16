"""Puntaje de typosquatting: qué tanto se parece un nombre a un paquete popular.

``score_name`` es una función pura: no hace E/S ni guarda estado, así que la
medición del detector (D4-6) puede llamarla directamente con cualquier umbral.
"""

import re
from enum import StrEnum
from typing import NamedTuple

from pydantic import BaseModel, Field, NonNegativeInt
from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein, JaroWinkler, Levenshtein

from supply_shield.analyzers.typosquatting.index import PopularIndex
from supply_shield.analyzers.typosquatting.keys import (
    confusable_key,
    separator_key,
    split_scope,
    strip_affixes,
)
from supply_shield.core.model_config import FROZEN

Weight = float

_MAX_EDIT_DISTANCE = 2
# Damerau-Levenshtein cuenta una transposición como 1 y Levenshtein como 2, así que
# DL <= 2 implica Levenshtein <= 4. Prefiltrar con Levenshtein (rápido en rapidfuzz)
# y sin ``limit`` no pierde ningún candidato que Damerau-Levenshtein encontraría.
_LEVENSHTEIN_PREFILTER = 2 * _MAX_EDIT_DISTANCE
_TRAILING_NUMBER = re.compile(r"\d+$")


class Technique(StrEnum):
    """Técnica de engaño que explica el parecido entre dos nombres."""

    SEPARATORS = "separators"
    CONFUSABLES = "confusables"
    SCOPE = "scope"
    EDIT_DISTANCE = "edit_distance"
    JARO_WINKLER = "jaro_winkler"
    AFFIX = "affix"


class TyposquattingWeights(BaseModel):
    """Peso de cada técnica y longitudes mínimas para aplicarlas.

    Los valores son provisionales: D4-6 los recalibra con un dataset etiquetado.
    ``docs/typosquatting.md`` explica cada uno.
    """

    model_config = FROZEN

    separators: Weight = Field(default=0.95, ge=0, le=1)
    confusables: Weight = Field(default=0.95, ge=0, le=1)
    scope: Weight = Field(default=0.90, ge=0, le=1)
    edit_distance_1: Weight = Field(default=0.90, ge=0, le=1)
    edit_distance_2: Weight = Field(default=0.75, ge=0, le=1)
    jaro_winkler: Weight = Field(default=0.80, ge=0, le=1)
    affix: Weight = Field(default=0.80, ge=0, le=1)
    numbered_variant: Weight = Field(default=0.70, ge=0, le=1)
    agreement_bonus: Weight = Field(default=0.05, ge=0, le=1)
    jaro_winkler_min_similarity: float = Field(default=0.92, ge=0, le=1)
    min_fuzzy_length: NonNegativeInt = 4
    edit_distance_1_min_length: NonNegativeInt = 5
    edit_distance_2_min_length: NonNegativeInt = 8


DEFAULT_WEIGHTS = TyposquattingWeights()


class TyposquatMatch(BaseModel):
    """Paquete popular al que más se parece un nombre, con su puntaje de 0 a 1.

    ``techniques`` va de la señal más fuerte a la más débil.
    """

    model_config = FROZEN

    name: str
    target: str
    target_rank: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    techniques: tuple[Technique, ...] = Field(min_length=1)


def score_name(
    name: str,
    index: PopularIndex,
    *,
    allowed: frozenset[str] = frozenset(),
    weights: TyposquattingWeights = DEFAULT_WEIGHTS,
) -> TyposquatMatch | None:
    """Devuelve el paquete popular que ``name`` imita, o ``None`` si no imita ninguno.

    ``name`` debe venir normalizado. Un nombre popular o permitido nunca se marca:
    un paquete popular no es typosquat de otro. Entre varios objetivos gana el de
    mayor puntaje y, si empatan, el más popular.
    """
    if name in index or name in allowed:
        return None
    affix_bases = strip_affixes(name)
    best: _Scored | None = None
    for target in _candidates(name, index, weights, affix_bases):
        scored = _score_target(name, target, index, weights, affix_bases)
        if scored is not None and (best is None or scored.beats(best)):
            best = scored
    if best is None:
        return None
    return TyposquatMatch(
        name=name,
        target=best.target,
        target_rank=best.rank,
        score=best.score,
        techniques=best.techniques,
    )


class _Scored(NamedTuple):
    """Puntaje de un objetivo. Más liviano que ``TyposquatMatch`` para el bucle interno."""

    score: float
    rank: int
    target: str
    techniques: tuple[Technique, ...]

    def beats(self, other: "_Scored") -> bool:
        return (self.score, -self.rank) > (other.score, -other.rank)


def _candidates(
    name: str,
    index: PopularIndex,
    weights: TyposquattingWeights,
    affix_bases: frozenset[str],
) -> set[str]:
    found = set(index.same_separator_key(name))
    found.update(index.same_confusable_key(name))
    found.update(base for base in affix_bases if base in index)
    if len(name) >= weights.min_fuzzy_length:
        found.update(
            choice
            for choice, _, _ in process.extract(
                name,
                index.names,
                scorer=Levenshtein.distance,
                score_cutoff=_LEVENSHTEIN_PREFILTER,
                limit=None,
            )
        )
        found.update(
            choice
            for choice, _, _ in process.extract(
                name,
                index.names,
                scorer=JaroWinkler.similarity,
                score_cutoff=weights.jaro_winkler_min_similarity,
                limit=None,
            )
        )
    return found


def _score_target(
    name: str,
    target: str,
    index: PopularIndex,
    weights: TyposquattingWeights,
    affix_bases: frozenset[str],
) -> _Scored | None:
    signals = _key_signals(name, target, weights)
    signals.update(_distance_signals(name, target, weights))
    if target in affix_bases:
        signals[Technique.AFFIX] = weights.affix
    if not signals:
        return None
    ranked = sorted(signals.items(), key=lambda item: item[1], reverse=True)
    strongest = ranked[0][1]
    score = min(1.0, strongest + weights.agreement_bonus * (len(ranked) - 1))
    return _Scored(
        score=round(score, 4),
        rank=index.rank(target),
        target=target,
        techniques=tuple(technique for technique, _ in ranked),
    )


def _key_signals(name: str, target: str, weights: TyposquattingWeights) -> dict[Technique, Weight]:
    signals: dict[Technique, Weight] = {}
    name_scope, name_bare = split_scope(name)
    target_scope, target_bare = split_scope(target)
    if separator_key(name) == separator_key(target):
        # Mismo nombre salvo separadores. Si solo uno de los dos tiene scope, el
        # engaño es quitar o poner el ``@scope/`` (``types-node`` por ``@types/node``).
        if (name_scope is None) != (target_scope is None):
            signals[Technique.SCOPE] = weights.scope
        else:
            signals[Technique.SEPARATORS] = weights.separators
    elif min(len(name), len(target)) >= weights.min_fuzzy_length and confusable_key(
        name
    ) == confusable_key(target):
        signals[Technique.CONFUSABLES] = weights.confusables
    if (
        name_scope is not None
        and target_scope is not None
        and name_bare == target_bare
        and name_scope != target_scope
        and DamerauLevenshtein.distance(name_scope, target_scope) <= _MAX_EDIT_DISTANCE
    ):
        # Mismo paquete bajo un scope mal escrito (``@typse/node`` por ``@types/node``).
        signals[Technique.SCOPE] = weights.scope
    return signals


def _distance_signals(
    name: str, target: str, weights: TyposquattingWeights
) -> dict[Technique, Weight]:
    signals: dict[Technique, Weight] = {}
    shortest = min(len(name), len(target))
    if _TRAILING_NUMBER.sub("", name) == _TRAILING_NUMBER.sub("", target):
        # Solo cambia el número final (``eventemitter2``/``eventemitter3``,
        # ``nvidia-cudnn-cu11``/``-cu12``): casi siempre son series legítimas.
        # Se registra, pero no alcanza para bloquear por sí solo.
        signals[Technique.EDIT_DISTANCE] = weights.numbered_variant
        return signals
    distance = DamerauLevenshtein.distance(name, target, score_cutoff=_MAX_EDIT_DISTANCE)
    if distance == 1 and shortest >= weights.edit_distance_1_min_length:
        signals[Technique.EDIT_DISTANCE] = weights.edit_distance_1
    elif distance == _MAX_EDIT_DISTANCE and shortest >= weights.edit_distance_2_min_length:
        signals[Technique.EDIT_DISTANCE] = weights.edit_distance_2
    if shortest >= weights.min_fuzzy_length:
        similarity = JaroWinkler.similarity(name, target)
        if similarity >= weights.jaro_winkler_min_similarity:
            signals[Technique.JARO_WINKLER] = weights.jaro_winkler * similarity
    return signals
