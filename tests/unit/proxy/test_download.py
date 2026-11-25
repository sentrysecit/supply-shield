"""Unit tests for the streaming download helper with SHA-512 verification."""

import gzip
import hashlib
from hashlib import sha512

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.proxy.download import (
    UpstreamIntegrityError,
    UpstreamTooLargeError,
    open_upstream_download,
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


# --- open_upstream_download -------------------------------------------------


@respx.mock
async def test_open_requests_identity_encoding() -> None:
    body = b"artifact"
    route = respx.get(URL).respond(200, content=body)
    settings = _settings()
    async with build_upstream_client(settings) as client:
        download = await open_upstream_download(client, URL, sha512(body).hexdigest(), settings)
        received = b"".join([chunk async for chunk in download.iter_verified()])
    assert received == body
    assert route.calls[0].request.headers["accept-encoding"] == "identity"


@respx.mock
async def test_open_rejects_compressed_response_before_reading() -> None:
    respx.get(URL).respond(
        200, content=gzip.compress(b"artifact"), headers={"content-encoding": "gzip"}
    )
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError, match="content-encoding"):
            await open_upstream_download(client, URL, "0" * 128, settings)


@respx.mock
async def test_open_accepts_explicit_identity_encoding() -> None:
    body = b"identity"
    respx.get(URL).respond(200, content=body, headers={"content-encoding": "identity"})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        received = await _collect(client, URL, sha512(body).hexdigest(), settings)
    assert received == body


@respx.mock
async def test_open_wraps_transport_errors() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError):
            await open_upstream_download(client, URL, "0" * 128, settings)


@respx.mock
async def test_open_raises_status_errors_before_the_body_is_read() -> None:
    respx.get(URL).respond(503)
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError, match="503"):
            await open_upstream_download(client, URL, "0" * 128, settings)


@respx.mock
async def test_iter_verified_cuts_undeclared_body_over_limit() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, stream=httpx.ByteStream(b"x" * 64)))
    settings = _settings(upstream_max_response_bytes=32)
    async with build_upstream_client(settings) as client:
        download = await open_upstream_download(client, URL, "0" * 128, settings)
        with pytest.raises(UpstreamTooLargeError):
            async for _ in download.iter_verified():
                pass


@respx.mock
async def test_verifies_with_sha1_when_asked() -> None:
    body = b"legacy-artifact"
    respx.get(URL).respond(200, content=body)
    expected = hashlib.sha1(body, usedforsecurity=False).hexdigest()
    settings = _settings()
    async with build_upstream_client(settings) as client:
        download = await open_upstream_download(client, URL, expected, settings, algorithm="sha1")
        received = b"".join([chunk async for chunk in download.iter_verified()])
    assert received == body


@respx.mock
async def test_sha1_mismatch_raises() -> None:
    respx.get(URL).respond(200, content=b"tampered")
    expected = hashlib.sha1(b"original", usedforsecurity=False).hexdigest()
    settings = _settings()
    async with build_upstream_client(settings) as client:
        download = await open_upstream_download(client, URL, expected, settings, algorithm="sha1")
        with pytest.raises(UpstreamIntegrityError, match="SHA1"):
            async for _ in download.iter_verified():
                pass


@respx.mock
async def test_aclose_releases_an_unread_download() -> None:
    respx.get(URL).respond(200, content=b"never-read")
    settings = _settings()
    async with build_upstream_client(settings) as client:
        download = await open_upstream_download(client, URL, "0" * 128, settings)
        await download.aclose()
        await download.aclose()
