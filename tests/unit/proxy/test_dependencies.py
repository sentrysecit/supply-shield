"""Unit tests for the shared upstream client wiring: dependency and app lifespan."""

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from supply_shield.config import Settings
from supply_shield.main import create_app
from supply_shield.proxy.dependencies import get_upstream_client


def _request_for(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app, "headers": []})


async def test_dependency_returns_the_app_client() -> None:
    app = create_app(Settings(_env_file=None))
    client = app.state.upstream_client
    try:
        assert isinstance(client, httpx.AsyncClient)
        assert get_upstream_client(_request_for(app)) is client
    finally:
        await client.aclose()


def test_dependency_fails_loudly_without_a_client() -> None:
    with pytest.raises(RuntimeError, match="Upstream client not initialized"):
        get_upstream_client(_request_for(FastAPI()))


async def test_lifespan_closes_the_shared_upstream_client() -> None:
    app = create_app(Settings(_env_file=None))
    client: httpx.AsyncClient = app.state.upstream_client
    async with app.router.lifespan_context(app):
        assert not client.is_closed
    assert client.is_closed
