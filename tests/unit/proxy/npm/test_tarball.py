"""Unit tests for npm tarball rewriting and verified download."""

import base64
import hashlib
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.main import create_app
from supply_shield.proxy.npm.packument import integrity_to_hex, rewrite_tarballs

TARBALL_BYTES = b"express-tarball-content-here"
RAW_DIGEST = hashlib.sha512(TARBALL_BYTES).digest()
INTEGRITY = f"sha512-{base64.b64encode(RAW_DIGEST).decode()}"
EXPECTED_HEX = RAW_DIGEST.hex()

PACKUMENT = {
    "name": "express",
    "versions": {
        "4.18.2": {
            "version": "4.18.2",
            "dist": {
                "tarball": "https://registry.npmjs.org/express/-/express-4.18.2.tgz",
                "integrity": INTEGRITY,
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


# --- rewrite_tarballs -----------------------------------------------------


def test_rewrite_tarballs_rewrites_urls() -> None:
    packument = {
        "versions": {
            "1.0.0": {
                "dist": {
                    "tarball": "https://registry.npmjs.org/express/-/express-1.0.0.tgz",
                },
            },
        },
    }
    result = rewrite_tarballs(packument, "http://proxy:8000")
    assert (
        result["versions"]["1.0.0"]["dist"]["tarball"]
        == "http://proxy:8000/npm/express/-/express-1.0.0.tgz"
    )


def test_rewrite_tarballs_handles_scoped() -> None:
    packument = {
        "versions": {
            "18.0.0": {
                "dist": {
                    "tarball": "https://registry.npmjs.org/@types/node/-/node-18.0.0.tgz",
                },
            },
        },
    }
    result = rewrite_tarballs(packument, "http://proxy:8000")
    assert (
        result["versions"]["18.0.0"]["dist"]["tarball"]
        == "http://proxy:8000/npm/@types/node/-/node-18.0.0.tgz"
    )


def test_integrity_to_hex_converts_base64() -> None:
    assert integrity_to_hex(INTEGRITY) == EXPECTED_HEX


def test_integrity_to_hex_passes_hex_through() -> None:
    assert integrity_to_hex("ABCDEF") == "abcdef"


# --- packument route rewrites --------------------------------------------


@respx.mock
async def test_get_packument_rewrites_tarball_urls(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    response = await client.get("/npm/express")
    assert response.status_code == 200
    tarball = response.json()["versions"]["4.18.2"]["dist"]["tarball"]
    assert tarball.startswith("http://localhost:8000/npm/express/-/")
    assert "registry.npmjs.org" not in tarball


# --- tarball route -------------------------------------------------------


@respx.mock
async def test_get_tarball_serves_verified_artifact(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    respx.get("https://registry.npmjs.org/express/-/express-4.18.2.tgz").respond(
        200,
        content=TARBALL_BYTES,
    )
    response = await client.get("/npm/express/-/express-4.18.2.tgz")
    assert response.status_code == 200
    assert response.content == TARBALL_BYTES


@respx.mock
async def test_get_tarball_404_when_package_missing(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/missing-pkg").respond(404, json={"error": "Not found"})
    response = await client.get("/npm/missing-pkg/-/missing-pkg-1.0.0.tgz")
    assert response.status_code == 502


@respx.mock
async def test_get_tarball_502_when_filename_not_in_packument(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/express").respond(200, json=PACKUMENT)
    response = await client.get("/npm/express/-/nonexistent-1.0.0.tgz")
    assert response.status_code == 502


@respx.mock
async def test_get_scoped_tarball_serves_verified_artifact(client: httpx.AsyncClient) -> None:
    scoped_bytes = b"scoped-tarball"
    scoped_raw = hashlib.sha512(scoped_bytes).digest()
    scoped_integrity = f"sha512-{base64.b64encode(scoped_raw).decode()}"
    scoped_packument = {
        "name": "@types/node",
        "versions": {
            "18.0.0": {
                "version": "18.0.0",
                "dist": {
                    "tarball": "https://registry.npmjs.org/@types/node/-/node-18.0.0.tgz",
                    "integrity": scoped_integrity,
                },
            },
        },
    }
    respx.get("https://registry.npmjs.org/@types/node").respond(200, json=scoped_packument)
    respx.get("https://registry.npmjs.org/@types/node/-/node-18.0.0.tgz").respond(
        200,
        content=scoped_bytes,
    )
    response = await client.get("/npm/@types/node/-/node-18.0.0.tgz")
    assert response.status_code == 200
    assert response.content == scoped_bytes
