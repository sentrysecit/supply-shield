"""Unit tests for package name validation and authentication in npm proxy routes."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest
import respx

from supply_shield.proxy.npm.packument import packument_url, validate_package_name

NpmClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


# --- validate_package_name unit tests ----------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "express",
        "@types/node",
        "lodash.merge",
        "JSONStream",
        "socket.io",
        "a~b",
        "@scope/-dash",
        "x" * 214,
    ],
)
def test_validate_package_name_accepts_npm_names(name: str) -> None:
    validate_package_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "..",
        "pkg\x00evil",
        "foo\nbar",
        "foo bar",
        "-/v1/search?text=foo",
        "a?b",
        "a#b",
        "a%2fb",
        "%2e%2e",
        "express/latest",
        "@scope",
        "@/name",
        "@scope/name/extra",
        ".hidden",
        "_private",
        "ñoño-pkg",
        "@élite/lib",
        "日本語パッケージ",
        "",
        "x" * 215,
    ],
)
def test_validate_package_name_rejects_anything_else(name: str) -> None:
    with pytest.raises(ValueError, match="invalid npm package name"):
        validate_package_name(name)


def test_packument_url_percent_encodes_the_name() -> None:
    assert packument_url("@types/node") == "https://registry.npmjs.org/@types/node"
    assert packument_url("it's(ok)") == "https://registry.npmjs.org/it%27s%28ok%29"


# --- route-level validation tests --------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "path",
    [
        "/npm/..%2fevil",
        "/npm/pkg%00evil",
        "/npm/foo%0Abar",
        "/npm/-%252Fv1%252Fsearch%253Ftext%253Dfoo",
        "/npm/%252e%252e",
        "/npm/express/latest",
        "/npm/..%2fevil/-/evil-1.0.0.tgz",
        "/npm/pkg%00evil/-/pkg-1.0.0.tgz",
        "/npm/foo%0Abar/-/foo-1.0.0.tgz",
        "/npm/scope/name/-/name-1.0.0.tgz",
    ],
)
async def test_invalid_names_return_400_without_upstream_call(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.get(path)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid package name"}
    assert not respx.calls


# --- authentication ----------------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "path",
    ["/npm/express", "/npm/@types%2fnode", "/npm/express/-/express-4.18.2.tgz"],
)
async def test_routes_require_a_project_token(npm_client: NpmClientFactory, path: str) -> None:
    async with npm_client(token=None) as client:
        response = await client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer, Basic"
    assert not respx.calls


@respx.mock
async def test_routes_reject_an_invalid_token(npm_client: NpmClientFactory) -> None:
    async with npm_client(token="not-the-token") as client:  # noqa: S106 - token de prueba
        response = await client.get("/npm/express")
    assert response.status_code == 401
    assert not respx.calls
