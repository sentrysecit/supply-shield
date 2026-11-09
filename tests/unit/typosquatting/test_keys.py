"""Claves de comparación: separadores, letras parecidas, scope y afijos."""

import pytest

from supply_shield.analyzers.typosquatting.keys import (
    confusable_key,
    separator_key,
    split_scope,
    strip_affixes,
)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("cross-env", "crossenv"),
        ("crossenv", "crossenv"),
        ("lodash.merge", "lodashmerge"),
        ("snake_case", "snakecase"),
        ("@types/node", "typesnode"),
        ("types-node", "typesnode"),
    ],
)
def test_separator_key_drops_separators_and_scope_marks(name: str, key: str) -> None:
    assert separator_key(name) == key


@pytest.mark.parametrize(
    ("lookalike", "original"),
    [
        ("jeilyfish", "jellyfish"),
        ("rnoment", "moment"),
        ("vvebpack", "webpack"),
        ("l0dash", "lodash"),
        ("1odash", "lodash"),
        ("cross_env", "cross-env"),
    ],
)
def test_confusable_key_unifies_lookalike_letters(lookalike: str, original: str) -> None:
    assert confusable_key(lookalike) == confusable_key(original)


def test_confusable_key_keeps_cl_and_d_apart() -> None:
    # "cl" -> "d" se dejó fuera: confunde pares legítimos.
    assert confusable_key("clone") != confusable_key("done")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("@types/node", ("types", "node")),
        ("@babel/core", ("babel", "core")),
        ("lodash", (None, "lodash")),
        ("@no-slash", (None, "@no-slash")),
    ],
)
def test_split_scope(name: str, expected: tuple[str | None, str]) -> None:
    assert split_scope(name) == expected


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("python3-dateutil", "dateutil"),
        ("python-requests", "requests"),
        ("py-requests", "requests"),
        ("pyrequests", "requests"),
        ("node-lodash", "lodash"),
        ("js-yaml", "yaml"),
        ("requests-python", "requests"),
        ("requests-py", "requests"),
        ("lodash-js", "lodash"),
        ("lodash.js", "lodash"),
        ("lodashjs", "lodash"),
        ("requests-dev", "requests"),
        ("requests-lib", "requests"),
    ],
)
def test_strip_affixes_finds_the_base_name(name: str, base: str) -> None:
    assert base in strip_affixes(name)


def test_strip_affixes_ignores_names_without_affixes_and_tiny_bases() -> None:
    assert strip_affixes("lodash") == frozenset()
    assert strip_affixes("pyab") == frozenset()
