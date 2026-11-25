"""Unit tests for structured logging in npm proxy routes (S9-S11).

S9 y S10 comprueban stdout porque configure_logging usa cache_logger_on_first_use=True:
los loggers de routes.py pueden estar cacheados antes de que capture_logs() reemplace
el pipeline, por lo que los eventos llegan a stdout en lugar de al capturador interno.
S11 parchea _log directamente para evitar depender del nivel de log global (INFO filtra DEBUG).
"""

import base64
import hashlib
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.main import create_app
from supply_shield.proxy.npm.packument import rewrite_tarballs

_TOKEN = "npm-logging-token"  # noqa: S105 - token de prueba

_TARBALL_BYTES = b"express-tarball-logging-test"
_RAW_DIGEST = hashlib.sha512(_TARBALL_BYTES).digest()
_INTEGRITY = f"sha512-{base64.b64encode(_RAW_DIGEST).decode()}"

_PACKUMENT = {
    "name": "express",
    "versions": {
        "4.18.2": {
            "version": "4.18.2",
            "dist": {
                "tarball": "https://registry.npmjs.org/express/-/express-4.18.2.tgz",
                "integrity": _INTEGRITY,
            },
        },
    },
    "dist-tags": {"latest": "4.18.2"},
}


class _TokenStore:
    async def verify(self, token: str) -> str | None:
        return "logging-tests" if token == _TOKEN else None


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Cliente contra la app completa, sin red ni ``.env``, con un token de proyecto."""
    app = create_app(Settings(_env_file=None))
    app.state.token_store = _TokenStore()
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as c:
        yield c
    await app.state.upstream_client.aclose()


# --- S9: packument request logs -----------------------------------------------


@respx.mock
async def test_packument_request_emits_structlog_events(
    client: httpx.AsyncClient, capfd: pytest.CaptureFixture[str]
) -> None:
    """S9: GET /npm/express emite los eventos structlog de petición y respuesta."""
    respx.get("https://registry.npmjs.org/express").respond(200, json=_PACKUMENT)

    response = await client.get("/npm/express")
    captured = capfd.readouterr()

    assert response.status_code == 200
    assert "npm.packument.request" in captured.out
    assert "npm.packument.served" in captured.out
    assert "express" in captured.out


# --- S10: tarball request logs -----------------------------------------------


@respx.mock
async def test_tarball_request_emits_structlog_events(
    client: httpx.AsyncClient, capfd: pytest.CaptureFixture[str]
) -> None:
    """S10: GET tarball emite los eventos structlog de petición y streaming."""
    respx.get("https://registry.npmjs.org/express").respond(200, json=_PACKUMENT)
    respx.get("https://registry.npmjs.org/express/-/express-4.18.2.tgz").respond(
        200, content=_TARBALL_BYTES
    )

    response = await client.get("/npm/express/-/express-4.18.2.tgz")
    captured = capfd.readouterr()

    assert response.status_code == 200
    assert "npm.tarball.request" in captured.out
    assert "npm.tarball.streaming" in captured.out
    assert "express" in captured.out


# --- S11: rewrite_tarballs debug log ------------------------------------------


def test_rewrite_tarballs_emits_debug_log() -> None:
    """S11: rewrite_tarballs llama _log.debug con el evento y el conteo correcto.

    Se parchea _log directamente porque el nivel INFO por defecto filtrarí a debug
    antes de llegar a cualquier capturador de structlog.
    """
    packument = {
        "name": "mylib",
        "versions": {
            "1.0.0": {"dist": {"tarball": "https://registry.npmjs.org/mylib/-/mylib-1.0.0.tgz"}},
            "2.0.0": {"dist": {"tarball": "https://registry.npmjs.org/mylib/-/mylib-2.0.0.tgz"}},
        },
    }

    mock_log: MagicMock = MagicMock()
    with patch("supply_shield.proxy.npm.packument._log", mock_log):
        rewrite_tarballs(packument, "http://proxy:8000")

    mock_log.debug.assert_called_once_with(
        "npm.packument.tarballs_rewritten", package="mylib", count=2
    )
