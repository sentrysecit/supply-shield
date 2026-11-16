"""Fixtures compartidos de pytest."""

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest

from supply_shield.analyzers.typosquatting import (
    PopularIndex,
    PopularPackages,
    PopularPackagesSource,
)
from supply_shield.config import Settings
from supply_shield.core import Ecosystem
from supply_shield.db import session as db_session
from supply_shield.main import create_app


@pytest.fixture(autouse=True)
def _restore_database_state() -> Iterator[None]:
    """Deja el motor global de ``supply_shield.db`` como estaba antes de cada prueba.

    ``create_app`` con la API de administración habilitada llama a ``init_engine``,
    que guarda el motor en un estado de módulo. Sin esto, una prueba que arranca la
    app con un ``.env`` que tiene ``SUPPLY_SHIELD_ADMIN_TOKEN_HASH`` (las e2e lo
    leen) deja ese motor a las pruebas que vienen después.
    """
    engine, factory = db_session._state.engine, db_session._state.session_factory
    yield
    db_session._state.engine, db_session._state.session_factory = engine, factory


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP conectado directo a la app ASGI, sin red ni ``.env``."""
    app = create_app(Settings(_env_file=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


# --- Detector de typosquatting: índices pequeños, sin las listas reales ---

IndexFactory = Callable[[Ecosystem, tuple[str, ...]], PopularIndex]

_PUBLISHED = datetime(2026, 10, 1, tzinfo=UTC)
_SOURCE = PopularPackagesSource(
    name="example",
    url="https://example.test/top.json",
    ranking="downloads",
    published_at=_PUBLISHED,
)


def _make_index(ecosystem: Ecosystem, names: tuple[str, ...]) -> PopularIndex:
    popular = PopularPackages(
        ecosystem=ecosystem, generated_at=_PUBLISHED, source=_SOURCE, packages=names
    )
    return PopularIndex(popular)


@pytest.fixture
def make_index() -> IndexFactory:
    """Construye un índice con los nombres dados, en ese orden de popularidad."""
    return _make_index


@pytest.fixture
def npm_index() -> PopularIndex:
    """Índice npm pequeño con objetivos típicos de typosquatting."""
    return _make_index(
        Ecosystem.NPM,
        (
            "ms",
            "lodash",
            "@types/node",
            "react",
            "cross-env",
            "moment",
            "eventemitter3",
            "webpack",
        ),
    )


@pytest.fixture
def pypi_index() -> PopularIndex:
    """Índice PyPI pequeño con objetivos típicos de typosquatting."""
    return _make_index(Ecosystem.PYPI, ("requests", "python-dateutil", "django", "jellyfish"))
