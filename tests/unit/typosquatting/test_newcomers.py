"""Cuarentena de los nombres que entran a la lista de paquetes populares."""

from collections.abc import Callable

import pytest

from supply_shield.analyzers.typosquatting import (
    NewcomerShortfallError,
    PopularIndex,
    SeverityThresholds,
    Technique,
    admit_newcomers,
)
from supply_shield.core import Ecosystem, Severity

IndexFactory = Callable[[Ecosystem, tuple[str, ...]], PopularIndex]


def test_lookalike_newcomer_is_held_and_the_next_name_takes_its_place(
    npm_index: PopularIndex,
) -> None:
    ranked = ("ms", "lodash", "crossenv", "react", "express", "cross-env", "webpack")

    review = admit_newcomers(ranked, npm_index, limit=5)

    assert review.packages == ("ms", "lodash", "react", "express", "cross-env")
    assert review.entered == ("express",)
    assert review.left == ("@types/node", "moment", "eventemitter3", "webpack")
    (held,) = review.held
    assert held.name == "crossenv"
    assert held.source_rank == 3
    assert held.match.target == "cross-env"
    assert held.match.techniques[0] is Technique.SEPARATORS
    assert held.severity is Severity.CRITICAL


def test_allowlisted_newcomer_enters(npm_index: PopularIndex) -> None:
    review = admit_newcomers(
        ("ms", "crossenv", "lodash"), npm_index, limit=3, allowed=frozenset({"crossenv"})
    )

    assert review.packages == ("ms", "crossenv", "lodash")
    assert review.entered == ("crossenv",)
    assert review.held == ()


def test_lookalikes_already_in_the_list_are_kept(make_index: IndexFactory) -> None:
    previous = make_index(Ecosystem.NPM, ("request", "requests", "cross-env", "crossenv"))

    review = admit_newcomers(previous.names, previous, limit=4)

    assert review.packages == previous.names
    assert review.entered == ()
    assert review.left == ()
    assert review.held == ()


def test_without_a_previous_list_everything_is_admitted() -> None:
    review = admit_newcomers(("cross-env", "crossenv", "lodash"), None, limit=3)

    assert review.packages == ("cross-env", "crossenv", "lodash")
    assert review.entered == review.packages
    assert review.left == ()
    assert review.held == ()


def test_weak_resemblance_does_not_hold(npm_index: PopularIndex) -> None:
    # Solo cambia el número final: el detector lo registra, pero no bloquea.
    review = admit_newcomers(("eventemitter2", "ms"), npm_index, limit=2)

    assert review.packages == ("eventemitter2", "ms")
    assert review.held == ()


def test_thresholds_decide_what_is_held(npm_index: PopularIndex) -> None:
    strict = SeverityThresholds(report=0.5, medium=0.6, high=0.7, critical=0.95)

    review = admit_newcomers(("eventemitter2", "ms"), npm_index, limit=1, thresholds=strict)

    assert review.packages == ("ms",)
    assert [held.name for held in review.held] == ["eventemitter2"]
    assert review.held[0].severity is Severity.HIGH


def test_names_past_the_cut_are_not_reviewed(npm_index: PopularIndex) -> None:
    review = admit_newcomers(("ms", "lodash", "crossenv"), npm_index, limit=2)

    assert review.packages == ("ms", "lodash")
    assert review.held == ()


def test_ranking_too_short_after_the_quarantine_fails(npm_index: PopularIndex) -> None:
    with pytest.raises(NewcomerShortfallError, match=r"only 2 names admitted \(1 held\)"):
        admit_newcomers(("ms", "crossenv", "lodash"), npm_index, limit=3)
