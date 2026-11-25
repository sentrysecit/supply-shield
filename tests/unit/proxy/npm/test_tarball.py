"""Unit tests for npm tarball rewriting, digest selection and verified download."""

import base64
import gzip
import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.proxy.download import UpstreamIntegrityError
from supply_shield.proxy.npm.packument import (
    ExpectedDigest,
    IntegrityUnavailableError,
    InvalidPackumentError,
    expected_digest,
    rewrite_tarballs,
)

NpmClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]
SettingsFactory = Callable[..., Settings]

PACKUMENT_URL = "https://registry.npmjs.org/express"
TARBALL_URL = "https://registry.npmjs.org/express/-/express-4.18.2.tgz"
TARBALL_PATH = "/npm/express/-/express-4.18.2.tgz"
TARBALL_BYTES = b"express-tarball-content-here"
RAW_SHA512 = hashlib.sha512(TARBALL_BYTES).digest()
RAW_SHA1 = hashlib.sha1(TARBALL_BYTES, usedforsecurity=False).digest()
INTEGRITY = f"sha512-{base64.b64encode(RAW_SHA512).decode()}"
SHA1_INTEGRITY = f"sha1-{base64.b64encode(RAW_SHA1).decode()}"


def _packument(dist: dict[str, Any]) -> dict[str, Any]:
    """Packument de express 4.18.2 con el ``dist`` dado y el tarball de siempre."""
    return {
        "name": "express",
        "versions": {"4.18.2": {"version": "4.18.2", "dist": {"tarball": TARBALL_URL, **dist}}},
        "dist-tags": {"latest": "4.18.2"},
    }


PACKUMENT = _packument({"integrity": INTEGRITY})


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


def test_rewrite_tarballs_skips_versions_without_dist() -> None:
    packument: dict[str, Any] = {"versions": {"1.0.0": "not-a-dict", "2.0.0": {"dist": None}}}
    assert rewrite_tarballs(packument, "http://proxy:8000") == packument


def test_rewrite_tarballs_rejects_non_object_versions() -> None:
    with pytest.raises(InvalidPackumentError):
        rewrite_tarballs({"versions": ["1.0.0"]}, "http://proxy:8000")


# --- expected_digest ------------------------------------------------------


def test_expected_digest_converts_sha512_integrity() -> None:
    assert expected_digest({"integrity": INTEGRITY}) == ExpectedDigest("sha512", RAW_SHA512.hex())


def test_expected_digest_picks_sha512_wherever_it_is() -> None:
    integrity = f"{SHA1_INTEGRITY} sha384-ignored {INTEGRITY}?opt"
    assert expected_digest({"integrity": integrity}).algorithm == "sha512"


def test_expected_digest_falls_back_to_sha1_integrity() -> None:
    assert expected_digest({"integrity": SHA1_INTEGRITY}) == ExpectedDigest("sha1", RAW_SHA1.hex())


def test_expected_digest_falls_back_to_hex_shasum() -> None:
    digest = expected_digest({"shasum": RAW_SHA1.hex().upper()})
    assert digest == ExpectedDigest("sha1", RAW_SHA1.hex())


@pytest.mark.parametrize(
    "dist",
    [
        {},
        {"integrity": "sha384-AAAA"},
        {"integrity": 42, "shasum": "not-hex"},
        {"integrity": "sha512-not*base64"},
        {"integrity": "sha512-AAAA"},
    ],
)
def test_expected_digest_rejects_dists_without_a_usable_digest(dist: dict[str, Any]) -> None:
    with pytest.raises(IntegrityUnavailableError):
        expected_digest(dist)


# --- packument route rewrites --------------------------------------------


@respx.mock
async def test_get_packument_rewrites_tarball_urls(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    response = await client.get("/npm/express")
    assert response.status_code == 200
    tarball = response.json()["versions"]["4.18.2"]["dist"]["tarball"]
    assert tarball.startswith("http://localhost:8000/npm/express/-/")
    assert "registry.npmjs.org" not in tarball


# --- tarball route -------------------------------------------------------


@respx.mock
async def test_get_tarball_serves_verified_artifact(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    tarball = respx.get(TARBALL_URL).respond(200, content=TARBALL_BYTES)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 200
    assert response.content == TARBALL_BYTES
    assert tarball.calls[0].request.headers["accept-encoding"] == "identity"


@respx.mock
async def test_get_tarball_resolves_with_the_abbreviated_packument(
    client: httpx.AsyncClient,
) -> None:
    packument = respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(200, content=TARBALL_BYTES)
    await client.get(TARBALL_PATH)
    assert "vnd.npm.install-v1+json" in packument.calls[0].request.headers["accept"]


@respx.mock
async def test_get_tarball_404_when_package_missing(client: httpx.AsyncClient) -> None:
    respx.get("https://registry.npmjs.org/missing-pkg").respond(404, json={"error": "Not found"})
    response = await client.get("/npm/missing-pkg/-/missing-pkg-1.0.0.tgz")
    assert response.status_code == 404


@respx.mock
async def test_get_tarball_404_when_filename_not_in_packument(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    response = await client.get("/npm/express/-/nonexistent-1.0.0.tgz")
    assert response.status_code == 404


@respx.mock
async def test_get_tarball_502_when_packument_lookup_fails(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(500)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502


@respx.mock
async def test_get_tarball_502_when_packument_is_malformed(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json={"name": "express", "versions": None})
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502
    assert response.json() == {"error": "invalid upstream response"}


# --- upstream failures before the response starts ----------------------------


@respx.mock
async def test_get_tarball_502_when_tarball_upstream_fails(client: httpx.AsyncClient) -> None:
    """A 503 from the CDN becomes a 502, never a 200 with a cut body."""
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(503)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502
    assert response.json() == {"error": "upstream unavailable"}


@respx.mock
async def test_get_tarball_502_when_tarball_connection_fails(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).mock(side_effect=httpx.ConnectError("refused"))
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502


@respx.mock
async def test_get_tarball_502_when_upstream_compresses_the_tarball(
    client: httpx.AsyncClient,
) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(
        200, content=gzip.compress(TARBALL_BYTES), headers={"content-encoding": "gzip"}
    )
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502


@respx.mock
async def test_get_tarball_502_when_declared_size_exceeds_limit(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(200, content=b"x" * 4096)
    async with npm_client(make_settings(upstream_max_response_bytes=2048)) as client:
        response = await client.get(TARBALL_PATH)
    assert response.status_code == 502
    assert response.json() == {"error": "upstream response too large"}


@respx.mock
async def test_get_tarball_403_when_tarball_redirects(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(302, headers={"Location": "https://evil.example.com/x"})
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 403


# --- legacy and malformed digests --------------------------------------------


@respx.mock
async def test_get_tarball_verifies_sha1_integrity(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=_packument({"integrity": SHA1_INTEGRITY}))
    respx.get(TARBALL_URL).respond(200, content=TARBALL_BYTES)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 200
    assert response.content == TARBALL_BYTES


@respx.mock
async def test_get_tarball_verifies_legacy_shasum(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=_packument({"shasum": RAW_SHA1.hex()}))
    respx.get(TARBALL_URL).respond(200, content=TARBALL_BYTES)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 200
    assert response.content == TARBALL_BYTES


@respx.mock
async def test_get_tarball_rejects_tampered_legacy_artifact(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=_packument({"shasum": RAW_SHA1.hex()}))
    respx.get(TARBALL_URL).respond(200, content=b"tampered")
    with pytest.raises(UpstreamIntegrityError):
        await client.get(TARBALL_PATH)


@respx.mock
async def test_get_tarball_uses_sha512_when_listed_after_sha1(client: httpx.AsyncClient) -> None:
    integrity = f"{SHA1_INTEGRITY} {INTEGRITY}"
    respx.get(PACKUMENT_URL).respond(200, json=_packument({"integrity": integrity}))
    respx.get(TARBALL_URL).respond(200, content=TARBALL_BYTES)
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 200


@respx.mock
async def test_get_tarball_502_when_integrity_is_malformed(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=_packument({"integrity": "sha512-%%%"}))
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502
    assert len(respx.calls) == 1  # the tarball itself is never requested


@respx.mock
async def test_get_tarball_502_when_no_digest_is_published(client: httpx.AsyncClient) -> None:
    respx.get(PACKUMENT_URL).respond(200, json=_packument({}))
    response = await client.get(TARBALL_PATH)
    assert response.status_code == 502


# --- allowlist enforcement (S3, S4) ----------------------------------------


@respx.mock
async def test_get_tarball_returns_403_when_host_blocked(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    """S3: host fuera de la allowlist → 403 sin contactar el upstream."""
    async with npm_client(make_settings(upstream_hosts=("pypi.org",))) as client:
        response = await client.get(TARBALL_PATH)
    assert response.status_code == 403
    assert not respx.calls


@respx.mock
async def test_get_scoped_tarball_returns_403_when_host_blocked(
    npm_client: NpmClientFactory, make_settings: SettingsFactory
) -> None:
    """S4: scoped tarball con host bloqueado → 403 sin contactar el upstream."""
    async with npm_client(make_settings(upstream_hosts=("pypi.org",))) as client:
        response = await client.get("/npm/@types/node/-/node-18.0.0.tgz")
    assert response.status_code == 403
    assert not respx.calls


# --- hash mismatch / integridad (S1, S2) ------------------------------------


@respx.mock
async def test_get_tarball_raises_on_hash_mismatch(client: httpx.AsyncClient) -> None:
    """S1: tarball con hash incorrecto → UpstreamIntegrityError se propaga por ASGI."""
    respx.get(PACKUMENT_URL).respond(200, json=PACKUMENT)
    respx.get(TARBALL_URL).respond(200, content=b"tampered-content")
    with pytest.raises(UpstreamIntegrityError):
        await client.get(TARBALL_PATH)


SCOPED_URL = "https://registry.npmjs.org/@types/node/-/node-18.0.0.tgz"


def _scoped_packument(content: bytes) -> dict[str, Any]:
    integrity = f"sha512-{base64.b64encode(hashlib.sha512(content).digest()).decode()}"
    return {
        "name": "@types/node",
        "versions": {
            "18.0.0": {
                "version": "18.0.0",
                "dist": {"tarball": SCOPED_URL, "integrity": integrity},
            },
        },
    }


@respx.mock
async def test_get_scoped_tarball_raises_on_hash_mismatch(client: httpx.AsyncClient) -> None:
    """S2: scoped tarball con hash incorrecto → UpstreamIntegrityError se propaga."""
    respx.get("https://registry.npmjs.org/@types/node").respond(
        200, json=_scoped_packument(b"good-bytes")
    )
    respx.get(SCOPED_URL).respond(200, content=b"tampered-scoped-content")
    with pytest.raises(UpstreamIntegrityError):
        await client.get("/npm/@types/node/-/node-18.0.0.tgz")


@respx.mock
async def test_get_scoped_tarball_serves_verified_artifact(client: httpx.AsyncClient) -> None:
    scoped_bytes = b"scoped-tarball"
    respx.get("https://registry.npmjs.org/@types/node").respond(
        200, json=_scoped_packument(scoped_bytes)
    )
    respx.get(SCOPED_URL).respond(200, content=scoped_bytes)
    response = await client.get("/npm/@types/node/-/node-18.0.0.tgz")
    assert response.status_code == 200
    assert response.content == scoped_bytes
