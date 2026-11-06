"""Unit tests for the shared upstream client and host allowlist."""

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    build_upstream_client,
    upstream_get,
    validate_upstream_url,
)

ALLOWED = ("registry.npmjs.org", "pypi.org", "files.pythonhosted.org")


def _settings(**overrides: object) -> Settings:
    """Fast test settings: near-zero backoff so retries do not slow the suite."""
    base: dict[str, object] = {
        "_env_file": None,
        "upstream_hosts": ALLOWED,
        "upstream_max_retries": 1,
        "upstream_backoff_factor": 0.001,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- validate_upstream_url -------------------------------------------------


def test_validate_accepts_allowed_https_host() -> None:
    parsed = validate_upstream_url("https://registry.npmjs.org/foo", ALLOWED)
    assert parsed.host == "registry.npmjs.org"


def test_validate_accepts_http_scheme() -> None:
    parsed = validate_upstream_url("http://pypi.org/simple/", ALLOWED)
    assert parsed.scheme == "http"


@pytest.mark.parametrize("url", ["ftp://registry.npmjs.org/x", "file:///etc/passwd"])
def test_validate_rejects_non_http_scheme(url: str) -> None:
    with pytest.raises(UpstreamHostError, match="scheme"):
        validate_upstream_url(url, ALLOWED)


def test_validate_rejects_credentials() -> None:
    with pytest.raises(UpstreamHostError, match="credentials"):
        validate_upstream_url("https://user:pass@registry.npmjs.org/x", ALLOWED)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost:8080/",
    ],
)
def test_validate_rejects_host_not_in_allowlist(url: str) -> None:
    with pytest.raises(UpstreamHostError, match="allowlist"):
        validate_upstream_url(url, ALLOWED)


# --- build_upstream_client -------------------------------------------------


async def test_build_client_reads_settings() -> None:
    connect = 4.0
    read = 12.0
    settings = _settings(
        upstream_read_timeout_seconds=read,
        upstream_connect_timeout_seconds=connect,
        upstream_max_connections=42,
    )
    async with build_upstream_client(settings) as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client.follow_redirects is False
        assert client.timeout.connect == connect
        assert client.timeout.read == read


# --- upstream_get ----------------------------------------------------------


@respx.mock
async def test_upstream_get_returns_response() -> None:
    url = "https://registry.npmjs.org/foo"
    respx.get(url).respond(200, json={"ok": True})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        response = await upstream_get(client, url, settings)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@respx.mock
async def test_upstream_get_rejects_disallowed_host_without_network() -> None:
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamHostError, match="allowlist"):
            await upstream_get(client, "https://evil.example.com/x", settings)
    assert not respx.calls


@respx.mock
async def test_upstream_get_rejects_redirect() -> None:
    url = "https://registry.npmjs.org/foo"
    respx.get(url).respond(302, headers={"Location": "https://evil.example.com/x"})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamHostError, match="redirect"):
            await upstream_get(client, url, settings)


@respx.mock
async def test_upstream_get_retries_on_5xx_then_succeeds() -> None:
    url = "https://registry.npmjs.org/foo"
    respx.get(url).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})],
    )
    settings = _settings(upstream_max_retries=1)
    async with build_upstream_client(settings) as client:
        response = await upstream_get(client, url, settings)
    assert response.status_code == 200
    assert len(respx.calls) == 2


@respx.mock
async def test_upstream_get_retries_on_connect_error_then_succeeds() -> None:
    url = "https://registry.npmjs.org/foo"
    respx.get(url).mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": True})],
    )
    settings = _settings(upstream_max_retries=1)
    async with build_upstream_client(settings) as client:
        response = await upstream_get(client, url, settings)
    assert response.status_code == 200


@respx.mock
async def test_upstream_get_raises_when_retries_exhausted() -> None:
    url = "https://registry.npmjs.org/foo"
    respx.get(url).mock(side_effect=httpx.ConnectTimeout("boom"))
    settings = _settings(upstream_max_retries=1)
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError):
            await upstream_get(client, url, settings)
    assert len(respx.calls) == 2
