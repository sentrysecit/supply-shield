"""Listas de paquetes populares: formato del archivo, validación y tiempo de carga."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from supply_shield.analyzers.typosquatting.popular import (
    PopularPackages,
    PopularPackagesSource,
    load_popular_packages,
)
from supply_shield.core import Ecosystem

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "popular"
EXPECTED_SIZE = 5000
MAX_LOAD_SECONDS = 1.0
PUBLISHED = datetime(2026, 10, 1, tzinfo=UTC)
SOURCE = PopularPackagesSource(
    name="example", url="https://example.test/top.json", ranking="downloads", published_at=PUBLISHED
)


def _popular(ecosystem: Ecosystem, packages: tuple[str, ...]) -> PopularPackages:
    return PopularPackages(
        ecosystem=ecosystem,
        generated_at=PUBLISHED,
        source=SOURCE,
        packages=packages,
    )


@pytest.mark.parametrize("ecosystem", list(Ecosystem))
def test_committed_list_loads_in_under_a_second_with_date_and_source(
    ecosystem: Ecosystem,
) -> None:
    started = time.perf_counter()
    popular = load_popular_packages(DATA_DIR / f"{ecosystem.value}.json")
    elapsed = time.perf_counter() - started

    assert elapsed < MAX_LOAD_SECONDS
    assert popular.ecosystem is ecosystem
    assert len(popular.packages) == EXPECTED_SIZE
    assert popular.source.name
    assert popular.source.url.startswith("https://")
    assert popular.generated_at.tzinfo is not None
    assert popular.source.published_at <= popular.generated_at


def test_committed_lists_start_with_well_known_packages() -> None:
    npm = load_popular_packages(DATA_DIR / "npm.json")
    pypi = load_popular_packages(DATA_DIR / "pypi.json")

    assert {"react", "lodash", "express"} <= set(npm.packages[:1000])
    assert {"requests", "numpy", "boto3"} <= set(pypi.packages[:1000])


def test_rejects_duplicate_names() -> None:
    with pytest.raises(ValidationError, match="unique"):
        _popular(Ecosystem.PYPI, ("requests", "requests"))


@pytest.mark.parametrize(
    ("ecosystem", "name"),
    [(Ecosystem.PYPI, "Django_REST"), (Ecosystem.NPM, "React"), (Ecosystem.PYPI, "bad name")],
)
def test_rejects_names_that_are_not_normalized(ecosystem: Ecosystem, name: str) -> None:
    with pytest.raises(ValidationError):
        _popular(ecosystem, (name,))


def test_rejects_an_empty_list_and_dates_without_time_zone() -> None:
    with pytest.raises(ValidationError):
        _popular(Ecosystem.NPM, ())
    with pytest.raises(ValidationError):
        PopularPackages(
            ecosystem=Ecosystem.NPM,
            generated_at=datetime(2026, 10, 1),
            source=SOURCE,
            packages=("react",),
        )


def test_same_ranking_ignores_dates_and_source_but_not_order() -> None:
    base = _popular(Ecosystem.NPM, ("react", "@types/node"))
    newer = base.model_copy(
        update={
            "generated_at": datetime(2026, 11, 1, tzinfo=UTC),
            "source": SOURCE.model_copy(update={"name": "other"}),
        }
    )

    assert base.same_ranking(newer)
    assert not base.same_ranking(_popular(Ecosystem.NPM, ("@types/node", "react")))
    assert not base.same_ranking(_popular(Ecosystem.PYPI, ("react", "types-node")))


def test_load_round_trips_a_written_file(tmp_path: Path) -> None:
    popular = _popular(Ecosystem.PYPI, ("requests", "typing-extensions"))
    path = tmp_path / "pypi.json"
    path.write_text(popular.model_dump_json(indent=2), encoding="utf-8")

    assert load_popular_packages(path) == popular
