"""Fixtures del espejo de PyPI: app mínima con el router, un token de prueba y respx.

Las pruebas no usan red: ``respx`` intercepta el cliente upstream compartido.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from supply_shield.config import Settings, get_settings
from supply_shield.proxy.pypi import PyPIRegistry
from supply_shield.proxy.pypi import router as pypi_router
from supply_shield.proxy.upstream import build_upstream_client

TOKEN = "pypi-test-token"  # noqa: S105 - token de prueba
PROJECT = "pypi-tests"


class FakeTokenStore:
    """Almacén de tokens con un único proyecto y sin hashing, para pruebas rápidas."""

    async def verify(self, token: str) -> str | None:
        """Devuelve el proyecto de prueba si el token coincide."""
        return PROJECT if token == TOKEN else None


def _settings(**overrides: object) -> Settings:
    """Ajustes sin ``.env``, con reintentos rápidos y el proxy en ``testserver``."""
    values: dict[str, object] = {
        "_env_file": None,
        "upstream_max_retries": 0,
        "upstream_backoff_factor": 0.001,
        "proxy_base_url": "http://testserver",
    }
    values.update(overrides)
    return Settings.model_validate(values)


PyPIClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def pypi_client() -> PyPIClientFactory:
    """Abre un cliente contra una app PyPI nueva (``token=None`` no envía Authorization)."""

    @asynccontextmanager
    async def factory(
        settings: Settings | None = None, *, token: str | None = TOKEN
    ) -> AsyncIterator[httpx.AsyncClient]:
        settings = settings or _settings()
        upstream = build_upstream_client(settings)
        app = FastAPI()
        app.dependency_overrides[get_settings] = lambda: settings
        app.state.token_store = FakeTokenStore()
        app.state.upstream_client = upstream
        app.state.pypi_registry = PyPIRegistry(upstream, settings)
        app.include_router(pypi_router)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver", headers=headers
            ) as http_client:
                yield http_client
        finally:
            await upstream.aclose()

    return factory


@pytest.fixture
async def client(pypi_client: PyPIClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente autenticado contra la app PyPI con los ajustes por defecto."""
    async with pypi_client() as http_client:
        yield http_client
