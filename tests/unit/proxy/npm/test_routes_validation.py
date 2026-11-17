"""Unit tests for package name validation in npm proxy routes (S5-S7)."""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.main import create_app
from supply_shield.proxy.npm.packument import validate_package_name


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the ASGI app, without network or .env."""
    app = create_app(Settings(_env_file=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# --- validate_package_name unit tests ----------------------------------------


def test_validate_package_name_rejects_path_traversal() -> None:
    """S5 (unidad): nombre con ``../`` lanza ValueError."""
    with pytest.raises(ValueError, match="no permitida"):
        validate_package_name("../etc/passwd")


def test_validate_package_name_rejects_null_byte() -> None:
    """S6 (unidad): nombre con byte nulo lanza ValueError."""
    with pytest.raises(ValueError, match="no permitida"):
        validate_package_name("pkg\x00evil")


def test_validate_package_name_accepts_valid_unicode() -> None:
    """S7 (unidad): nombre Unicode sin secuencias peligrosas no lanza."""
    validate_package_name("ñoño-pkg")
    validate_package_name("@élite/lib")
    validate_package_name("日本語パッケージ")


# --- route-level validation tests --------------------------------------------


@respx.mock
async def test_packument_returns_400_for_path_traversal(client: httpx.AsyncClient) -> None:
    """S5 (ruta): ``../evil`` en la URL del packument → 400 sin reenviar al upstream."""
    response = await client.get("/npm/..%2fevil")
    assert response.status_code == 400
    assert not respx.calls


@respx.mock
async def test_packument_returns_400_for_null_byte(client: httpx.AsyncClient) -> None:
    """S6 (ruta): nombre con null byte URL-encoded → 400 sin reenviar al upstream."""
    response = await client.get("/npm/pkg%00evil")
    assert response.status_code == 400
    assert not respx.calls


@respx.mock
async def test_tarball_returns_400_for_path_traversal(client: httpx.AsyncClient) -> None:
    """S5 (tarball): nombre con ``../`` → 400 sin reenviar al upstream."""
    response = await client.get("/npm/..%2fevil/-/evil-1.0.0.tgz")
    assert response.status_code == 400
    assert not respx.calls


@respx.mock
async def test_tarball_returns_400_for_null_byte(client: httpx.AsyncClient) -> None:
    """S6 (tarball): nombre con null byte → 400 sin reenviar al upstream."""
    response = await client.get("/npm/pkg%00evil/-/pkg-1.0.0.tgz")
    assert response.status_code == 400
    assert not respx.calls
