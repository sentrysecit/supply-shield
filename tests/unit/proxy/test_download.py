"""Unit tests for the streaming download helper with SHA-512 verification."""

from hashlib import sha512

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.proxy.download import (
    UpstreamIntegrityError,
    UpstreamTooLargeError,
    stream_upstream,
)
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    build_upstream_client,
)

URL = "https://files.pythonhosted.org/packages/ab/artifact.tar.gz"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "upstream_hosts": ("files.pythonhosted.org", "registry.npmjs.org", "pypi.org"),
        "upstream_max_retries": 0,
        "upstream_backoff_factor": 0.001,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _collect(
    client: httpx.AsyncClient,
    url: str,
    expected: str,
    settings: Settings,
) -> bytes:
    chunks: list[bytes] = []
    async for chunk in stream_upstream(client, url, expected, settings):
        chunks.append(chunk)
    return b"".join(chunks)


@respx.mock
async def test_stream_returns_intact_artifact() -> None:
    body = b"hello-upstream" * 16
    respx.get(URL).respond(200, content=body)
    expected = sha512(body).hexdigest()
    settings = _settings()
    async with build_upstream_client(settings) as client:
        received = await _collect(client, URL, expected, settings)
    assert received == body


@respx.mock
async def test_stream_accepts_prefixed_digest() -> None:
    body = b"prefixed-digest-payload"
    respx.get(URL).respond(200, content=body)
    expected = f"sha512-{sha512(body).hexdigest()}"
    settings = _settings()
    async with build_upstream_client(settings) as client:
        received = await _collect(client, URL, expected, settings)
    assert received == body


@respx.mock
async def test_stream_raises_on_hash_mismatch() -> None:
    body = b"tampered-artifact"
    respx.get(URL).respond(200, content=body)
    expected = sha512(b"different-content").hexdigest()
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamIntegrityError):
            await _collect(client, URL, expected, settings)


@respx.mock
async def test_stream_rejects_disallowed_host_without_network() -> None:
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamHostError, match="allowlist"):
            await _collect(client, "https://evil.example.com/x", "0" * 128, settings)
    assert not respx.calls


@respx.mock
async def test_stream_rejects_redirect() -> None:
    respx.get(URL).respond(302, headers={"Location": "https://evil.example.com/x"})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamHostError, match="redirect"):
            await _collect(client, URL, "0" * 128, settings)


@respx.mock
async def test_stream_raises_on_error_status() -> None:
    respx.get(URL).respond(404)
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError, match="status"):
            await _collect(client, URL, "0" * 128, settings)


@respx.mock
async def test_stream_aborts_when_size_limit_exceeded() -> None:
    body = b"x" * 100
    respx.get(URL).respond(200, content=body)
    settings = _settings(upstream_max_response_bytes=8)
    received = bytearray()

    async def consume() -> None:
        async for chunk in stream_upstream(client, URL, "0" * 128, settings):
            received.extend(chunk)

    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamTooLargeError):
            await consume()
    assert len(received) <= 8


@respx.mock
async def test_stream_succeeds_under_size_limit() -> None:
    body = b"small-payload"
    respx.get(URL).respond(200, content=body)
    expected = sha512(body).hexdigest()
    settings = _settings(upstream_max_response_bytes=1024)
    async with build_upstream_client(settings) as client:
        received = await _collect(client, URL, expected, settings)
    assert received == body
