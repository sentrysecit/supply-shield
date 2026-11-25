"""Fixtures del adaptador npm: app mínima con el router, un token de prueba y el cliente upstream.

Las pruebas no usan red: ``respx`` intercepta el cliente upstream compartido.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from supply_shield.config import Settings, get_settings
from supply_shield.proxy.npm.routes import router as npm_router
from supply_shield.proxy.upstream import build_upstream_client

TOKEN = "npm-test-token"  # noqa: S105 - token de prueba
PROJECT = "npm-tests"


class FakeTokenStore:
    """Almacén de tokens con un único proyecto y sin hashing, para pruebas rápidas."""

    async def verify(self, token: str) -> str | None:
        """Devuelve el proyecto de prueba si el token coincide."""
        return PROJECT if token == TOKEN else None


def _settings(**overrides: object) -> Settings:
    """Ajustes sin ``.env`` y con reintentos rápidos."""
    values: dict[str, object] = {
        "_env_file": None,
        "upstream_max_retries": 0,
        "upstream_backoff_factor": 0.001,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _build_app(settings: Settings, upstream_client: httpx.AsyncClient) -> FastAPI:
    """App con solo el router npm, el almacén de tokens falso y el cliente upstream dado."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.token_store = FakeTokenStore()
    app.state.upstream_client = upstream_client
    app.include_router(npm_router)
    return app


NpmClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Construye ajustes de prueba con los valores dados."""
    return _settings


@pytest.fixture
def npm_client() -> NpmClientFactory:
    """Abre un cliente contra una app npm nueva.

    Acepta ``settings`` (por defecto, sin reintentos), ``token`` (por defecto el
    válido; ``None`` no envía ``Authorization``) y ``upstream_client`` (por defecto,
    el que construye ``build_upstream_client``).
    """

    @asynccontextmanager
    async def factory(
        settings: Settings | None = None,
        *,
        token: str | None = TOKEN,
        upstream_client: httpx.AsyncClient | None = None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        settings = settings or _settings()
        upstream = upstream_client or build_upstream_client(settings)
        app = _build_app(settings, upstream)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver", headers=headers
            ) as client:
                yield client
        finally:
            await upstream.aclose()

    return factory


@pytest.fixture
async def client(npm_client: NpmClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente autenticado contra la app npm con los ajustes por defecto."""
    async with npm_client() as http_client:
        yield http_client
