"""Unit tests for the npm packument routes."""

import gzip
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import respx

from supply_shield.config import Settings

NpmClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]
SettingsFactory = Callable[..., Settings]

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
async def test_get_packument_returns_502_on_upstream_failure(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").mock(side_effect=httpx.ConnectError("boom"))
    response = await client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_get_packument_returns_403_on_disallowed_host(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    async with npm_client(make_settings(upstream_hosts=("pypi.org",))) as client:
        response = await client.get("/npm/express")
    assert response.status_code == 403
    assert not respx.calls


# --- version documents never reach the client -------------------------------


@respx.mock
async def test_version_document_path_is_rejected_without_upstream_call(
    client: httpx.AsyncClient,
) -> None:
    """``/npm/express/latest`` is a version doc whose ``dist.tarball`` is never rewritten."""
    response = await client.get("/npm/express/latest")
    assert response.status_code == 400
    assert not respx.calls


# --- malformed upstream responses -------------------------------------------


@respx.mock
async def test_packument_returns_502_when_upstream_body_is_not_json(
    client: httpx.AsyncClient,
) -> None:
    respx.get("https://registry.npmjs.org/express").respond(
        200, content=b"<html>maintenance</html>", headers={"content-type": "text/html"}
    )
    response = await client.get("/npm/express")
    assert response.status_code == 502
    assert response.json() == {"error": "invalid upstream response"}


@respx.mock
async def test_packument_returns_502_when_body_is_not_an_object(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=["not", "an", "object"])
    response = await client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_packument_returns_502_when_versions_is_null(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(
        200, json={"name": "express", "versions": None}
    )
    response = await client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_packument_returns_502_when_a_tarball_is_not_a_string(
    client: httpx.AsyncClient,
) -> None:
    packument = {"name": "express", "versions": {"1.0.0": {"dist": {"tarball": 42}}}}
    respx.get("https://registry.npmjs.org/express").respond(200, json=packument)
    response = await client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_packument_returns_502_when_a_tarball_is_not_a_url(client: httpx.AsyncClient) -> None:
    packument = {"name": "express", "versions": {"1.0.0": {"dist": {"tarball": "http://[::1"}}}}
    respx.get("https://registry.npmjs.org/express").respond(200, json=packument)
    response = await client.get("/npm/express")
    assert response.status_code == 502


@respx.mock
async def test_unpublished_packument_without_versions_is_served(
    client: httpx.AsyncClient,
) -> None:
    unpublished = {"name": "gone", "time": {"unpublished": {"time": "2020-01-01T00:00:00Z"}}}
    respx.get("https://registry.npmjs.org/gone").respond(200, json=unpublished)
    response = await client.get("/npm/gone")
    assert response.status_code == 200
    assert response.json()["name"] == "gone"


# --- transport errors and size limit ----------------------------------------


@respx.mock
async def test_packument_retries_a_dropped_connection(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    route = respx.get("https://registry.npmjs.org/express")
    route.side_effect = [
        httpx.RemoteProtocolError("connection reset"),
        httpx.Response(200, json=PACKUMENT),
    ]
    async with npm_client(make_settings(upstream_max_retries=1)) as client:
        response = await client.get("/npm/express")
    assert response.status_code == 200
    assert route.call_count == 2


@respx.mock
async def test_packument_returns_502_when_read_errors_exhaust_retries(
    client: httpx.AsyncClient,
) -> None:
    respx.get("https://registry.npmjs.org/express").mock(side_effect=httpx.ReadError("reset"))
    response = await client.get("/npm/express")
    assert response.status_code == 502
    assert response.json() == {"error": "upstream unavailable"}


@respx.mock
async def test_packument_returns_502_when_larger_than_the_limit(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    async with npm_client(make_settings(upstream_max_response_bytes=16)) as client:
        response = await client.get("/npm/express")
    assert response.status_code == 502
    assert response.json() == {"error": "upstream response too large"}


@respx.mock
async def test_packument_compressed_by_upstream_is_served_decoded(
    client: httpx.AsyncClient,
) -> None:
    body = gzip.compress(json.dumps(PACKUMENT).encode())
    respx.get("https://registry.npmjs.org/express").respond(
        200,
        content=body,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
    )
    response = await client.get("/npm/express")
    assert response.status_code == 200
    assert response.json()["name"] == "express"


# --- tarball URL rewriting ---------------------------------------------------


@respx.mock
async def test_trailing_slash_in_proxy_base_url_does_not_double_the_slash(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    async with npm_client(make_settings(proxy_base_url="http://proxy:8000/")) as client:
        response = await client.get("/npm/express")
    tarball = response.json()["versions"]["4.18.2"]["dist"]["tarball"]
    assert tarball == "http://proxy:8000/npm/express/-/express-4.18.2.tgz"


# --- shared upstream client --------------------------------------------------


async def test_routes_use_the_app_shared_upstream_client(npm_client: NpmClientFactory) -> None:
    """All requests go through ``app.state.upstream_client`` instead of a new client each."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=PACKUMENT)

    shared = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with npm_client(upstream_client=shared) as client:
        first = await client.get("/npm/express")
        second = await client.get("/npm/express")
    assert first.status_code == second.status_code == 200
    assert seen == ["https://registry.npmjs.org/express"] * 2
