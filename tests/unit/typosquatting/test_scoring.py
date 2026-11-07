"""Puntaje de parecido de un nombre con los paquetes populares."""

from collections.abc import Callable

import pytest

from supply_shield.analyzers.typosquatting import (
    PopularIndex,
    Technique,
    TyposquatMatch,
    TyposquattingWeights,
    score_name,
)
from supply_shield.core import Ecosystem

IndexFactory = Callable[[Ecosystem, tuple[str, ...]], PopularIndex]


def _match(name: str, index: PopularIndex) -> TyposquatMatch:
    match = score_name(name, index)
    assert match is not None, f"{name} should look like a popular package"
    return match


# --- Exenciones y nombres sin parecido ---


def test_popular_and_allowed_names_are_never_flagged(npm_index: PopularIndex) -> None:
    assert score_name("lodash", npm_index) is None
    assert score_name("loadsh", npm_index, allowed=frozenset({"loadsh"})) is None


def test_unrelated_names_have_no_match(npm_index: PopularIndex) -> None:
    assert score_name("a-totally-unrelated-name", npm_index) is None
    assert score_name("zz", npm_index) is None


# --- Cada técnica ---


def test_separators(npm_index: PopularIndex) -> None:
    match = _match("crossenv", npm_index)

    assert match.target == "cross-env"
    assert match.target_rank == 5
    assert match.techniques[0] is Technique.SEPARATORS
    assert match.score == 1.0


def test_separators_apply_to_short_names(npm_index: PopularIndex) -> None:
    match = _match("m-s", npm_index)

    assert match.target == "ms"
    assert match.techniques == (Technique.SEPARATORS,)
    assert match.score == 0.95


def test_confusables(npm_index: PopularIndex) -> None:
    match = _match("rnoment", npm_index)

    assert match.target == "moment"
    assert match.techniques == (Technique.CONFUSABLES,)
    assert match.score == 0.95


def test_confusables_are_ignored_for_short_names(npm_index: PopularIndex) -> None:
    # "rns" se lee como "ms", pero en nombres tan cortos el parecido es casualidad.
    assert score_name("rns", npm_index) is None


def test_scope_dropped_or_added(npm_index: PopularIndex) -> None:
    match = _match("types-node", npm_index)

    assert match.target == "@types/node"
    assert match.techniques[0] is Technique.SCOPE


def test_scope_typo(npm_index: PopularIndex) -> None:
    match = _match("@typse/node", npm_index)

    assert match.target == "@types/node"
    assert Technique.SCOPE in match.techniques
    assert match.score == 1.0


def test_a_popular_name_under_an_unrelated_scope_is_not_typosquatting(
    npm_index: PopularIndex,
) -> None:
    # Republicar un nombre bajo un scope propio es habitual; dependency confusion
    # (D5-4) cubre el caso peligroso.
    assert score_name("@acme-corp/lodash", npm_index) is None


def test_edit_distance_one(npm_index: PopularIndex) -> None:
    match = _match("loadsh", npm_index)

    assert match.target == "lodash"
    assert match.techniques[0] is Technique.EDIT_DISTANCE
    assert match.score == 0.95


def test_edit_distance_one_needs_five_characters(
    make_index: IndexFactory,
) -> None:
    index = make_index(Ecosystem.NPM, ("axio",))

    match = score_name("axoi", index)

    assert match is None or Technique.EDIT_DISTANCE not in match.techniques


def test_edit_distance_two_only_counts_for_long_names(pypi_index: PopularIndex) -> None:
    long_match = _match("pythn-dateuti", pypi_index)
    assert long_match.target == "python-dateutil"
    assert Technique.EDIT_DISTANCE in long_match.techniques
    assert long_match.score < 0.85

    assert score_name("djanqq", pypi_index) is None


def test_jaro_winkler_alone_is_a_weak_signal(make_index: IndexFactory) -> None:
    index = make_index(Ecosystem.NPM, ("react",))

    match = _match("reac", index)

    assert match.techniques == (Technique.JARO_WINKLER,)
    assert 0.6 < match.score < 0.8


def test_affix(pypi_index: PopularIndex) -> None:
    match = _match("py-requests", pypi_index)

    assert match.target == "requests"
    assert match.techniques == (Technique.AFFIX,)
    assert match.score == 0.8


def test_numbered_variants_are_only_a_weak_signal(npm_index: PopularIndex) -> None:
    match = _match("eventemitter2", npm_index)

    assert match.target == "eventemitter3"
    assert match.techniques == (Technique.EDIT_DISTANCE,)
    assert match.score == 0.7


def test_a_number_inside_the_name_is_not_a_numbered_variant(pypi_index: PopularIndex) -> None:
    match = _match("python3-dateutil", pypi_index)

    assert match.target == "python-dateutil"
    assert match.score >= 0.95


# --- Combinación ---


def test_each_extra_signal_adds_the_agreement_bonus(npm_index: PopularIndex) -> None:
    weights = TyposquattingWeights(
        separators=0.5, edit_distance_1=0.4, jaro_winkler=0.3, agreement_bonus=0.1
    )

    match = score_name("crossenv", npm_index, weights=weights)

    assert match is not None
    assert match.techniques == (
        Technique.SEPARATORS,
        Technique.EDIT_DISTANCE,
        Technique.JARO_WINKLER,
    )
    assert match.score == 0.7


def test_the_score_is_capped_at_one(npm_index: PopularIndex) -> None:
    assert _match("crossenv", npm_index).score == 1.0


def test_highest_score_wins(make_index: IndexFactory) -> None:
    # "cross-env" coincide por separadores; "crossenvy" solo por distancia.
    index = make_index(Ecosystem.NPM, ("crossenvy", "cross-env"))

    assert _match("crossenv", index).target == "cross-env"


@pytest.mark.parametrize("names", [("react-a", "react-b"), ("react-b", "react-a")])
def test_ties_go_to_the_most_popular_target(
    make_index: IndexFactory, names: tuple[str, str]
) -> None:
    index = make_index(Ecosystem.NPM, names)

    match = _match("react-c", index)

    assert match.target == names[0]
    assert match.target_rank == 1


def test_pypi_names_are_compared_after_normalization(pypi_index: PopularIndex) -> None:
    # PEP 503: "jeIlyfish" llega normalizado como "jeilyfish".
    match = _match("jeilyfish", pypi_index)

    assert match.target == "jellyfish"
    assert match.techniques[0] is Technique.CONFUSABLES
