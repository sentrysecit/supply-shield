"""Unit tests for the npm packument routes."""

from collections.abc import AsyncIterator

import fastapi
import httpx
import pytest
import respx

from supply_shield.config import Settings, get_settings
from supply_shield.main import create_app
from supply_shield.proxy.npm.routes import router as npm_router

PACKUMENT = {
    "name": "express",
    "versions": {
        "4.18.2": {
            "version": "4.18.2",
            "dist": {
                "tarball": "https://registry.npmjs.org/express/-/express-4.18.2.tgz",
                "integrity": "sha512-abc",
                "shasum": "deadbeef",
            },
        },
    },
    "dist-tags": {"latest": "4.18.2"},
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the ASGI app, with no network or .env involved."""
    app = create_app(Settings(_env_file=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def _app_with_settings(settings: Settings) -> tuple[fastapi.FastAPI, httpx.AsyncClient]:
    """Build an app + client with overridden settings (for error-path tests)."""
    app = fastapi.FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(npm_router)
    transport = httpx.ASGITransport(app=app)
    return app, httpx.AsyncClient(transport=transport, base_url="http://testserver")


@respx.mock
async def test_get_packument_returns_upstream_json(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    response = await client.get("/npm/express")
    assert response.status_code == 200
    assert response.json()["name"] == "express"


@respx.mock
async def test_get_packument_forwards_accept_header(client: httpx.AsyncClient) -> None:
    abbreviated = {"name": "express", "versions": {"4.18.2": {"version": "4.18.2"}}}
    route = respx.get("https://registry.npmjs.org/express").respond(
        200,
        json=abbreviated,
        headers={"content-type": "application/vnd.npm.install-v1+json"},
    )
    response = await client.get(
        "/npm/express",
        headers={"accept": "application/vnd.npm.install-v1+json"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.npm.install-v1+json"
    sent_accept = route.calls[0].request.headers.get("accept", "")
    assert "vnd.npm.install-v1+json" in sent_accept


@respx.mock
async def test_get_packument_returns_404_for_unknown_package(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/this-does-not-exist-xyz").respond(
        404, json={"error": "Not found"}
    )
    response = await client.get("/npm/this-does-not-exist-xyz")
    assert response.status_code == 404


@respx.mock
async def test_get_scoped_package_decodes_percent_2f(client: httpx.AsyncClient) -> None:
    scoped = {"name": "@types/node", "versions": {"18.0.0": {"version": "18.0.0"}}}
    route = respx.get("https://registry.npmjs.org/@types/node").respond(200, json=scoped)
    response = await client.get("/npm/@types%2fnode")
    assert response.status_code == 200
    assert response.json()["name"] == "@types/node"
    assert route.called


@respx.mock
async def test_get_packument_returns_502_on_upstream_failure() -> None:
    respx.get("https://registry.npmjs.org/express").mock(side_effect=httpx.ConnectError("boom"))
    settings = Settings(_env_file=None, upstream_max_retries=0)
    _, test_client = _app_with_settings(settings)
    async with test_client:
        response = await test_client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_get_packument_returns_403_on_disallowed_host() -> None:
    settings = Settings(_env_file=None, upstream_hosts=("pypi.org",))
    _, test_client = _app_with_settings(settings)
    async with test_client:
        response = await test_client.get("/npm/express")
    assert response.status_code == 403
    assert not respx.calls
