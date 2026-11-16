"""Autorización de la API de administración: solo el token de administrador la abre."""

import base64
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from structlog.typing import EventDict

from supply_shield.api.auth import hash_token
from supply_shield.config import Settings
from supply_shield.main import create_app

ADMIN_TOKEN = "ssa_test_admin_token"  # noqa: S105 - mismo token que conftest
PROJECT_TOKEN = "ss_frontend_test_token"  # noqa: S105 - mismo token que conftest
EXAMPLE_ID = "00000000-0000-0000-0000-000000000001"

ClientFactory = Callable[[FastAPI, str | None], httpx.AsyncClient]
SettingsFactory = Callable[..., Settings]


def _admin_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Cada operación de administración del OpenAPI, con ids de ejemplo en la ruta."""
    routes: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/v1/admin"):
            concrete = (
                path.replace("{sbom_id}", EXAMPLE_ID)
                .replace("{exception_id}", EXAMPLE_ID)
                .replace("{token_id}", EXAMPLE_ID)
                .replace("{project}", "frontend")
            )
            routes.extend((method.upper(), concrete) for method in operations)
    return routes


async def test_every_admin_route_rejects_a_request_without_token(
    admin_app: FastAPI, anonymous: httpx.AsyncClient
) -> None:
    routes = _admin_routes(admin_app)
    assert len(routes) == 13

    for method, path in routes:
        response = await anonymous.request(method, path, json={})

        assert response.status_code == httpx.codes.UNAUTHORIZED, (method, path)
        assert response.headers["WWW-Authenticate"].startswith("Bearer")
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong-token",
        f"Bearer {PROJECT_TOKEN}",
        f"Basic {base64.b64encode(f'admin:{ADMIN_TOKEN}'.encode()).decode()}",
        f"Token {ADMIN_TOKEN}",
        "Bearer    ",
        ADMIN_TOKEN,
    ],
    ids=["wrong", "project-token", "basic", "other-scheme", "blank", "no-scheme"],
)
async def test_only_the_admin_bearer_token_is_accepted(
    admin_app: FastAPI, client_for: ClientFactory, authorization: str
) -> None:
    async with client_for(admin_app, None) as client:
        response = await client.get(
            "/api/v1/admin/policy", headers={"Authorization": authorization}
        )

    assert response.status_code == httpx.codes.UNAUTHORIZED


async def test_the_admin_token_opens_the_api(admin: httpx.AsyncClient) -> None:
    response = await admin.get("/api/v1/admin/policy")

    assert response.status_code == httpx.codes.OK
    assert response.headers["Cache-Control"] == "no-store"


async def test_a_project_token_does_not_open_the_admin_api(
    admin_app: FastAPI, client_for: ClientFactory
) -> None:
    async with client_for(admin_app, PROJECT_TOKEN) as client:
        response = await client.get("/api/v1/admin/decisions")

    assert response.status_code == httpx.codes.UNAUTHORIZED
    assert response.json() == {"detail": "Token de administrador inválido."}


async def test_without_a_configured_hash_the_api_is_disabled(
    admin_settings: SettingsFactory, client_for: ClientFactory
) -> None:
    app = create_app(admin_settings(admin_token_hash=None))

    async with client_for(app, ADMIN_TOKEN) as client:
        response = await client.get("/api/v1/admin/policy")

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE
    assert "SUPPLY_SHIELD_ADMIN_TOKEN_HASH" in response.json()["detail"]
    assert app.state.admin_repository is None


async def test_an_empty_hash_also_disables_the_api(admin_settings: SettingsFactory) -> None:
    app = create_app(admin_settings(admin_token_hash=SecretStr("")))

    assert app.state.admin_config.token_hash is None


def test_a_plain_token_instead_of_a_hash_stops_the_startup(
    admin_settings: SettingsFactory,
) -> None:
    with pytest.raises(ValueError, match="hash produced by"):
        create_app(admin_settings(admin_token_hash=SecretStr(ADMIN_TOKEN)))


def test_the_hash_is_not_exposed_by_the_settings_repr(tmp_path: Path) -> None:
    stored = hash_token(ADMIN_TOKEN)
    settings = Settings(_env_file=None, admin_token_hash=SecretStr(stored))

    assert stored not in repr(settings)


async def test_admin_tokens_never_reach_the_logs(
    admin_app: FastAPI, client_for: ClientFactory, admin_logs: list[EventDict]
) -> None:
    async with client_for(admin_app, "a-wrong-secret") as wrong:
        await wrong.get("/api/v1/admin/policy")
    async with client_for(admin_app, ADMIN_TOKEN) as right:
        await right.post("/api/v1/admin/policy/reload")

    events = [entry["event"] for entry in admin_logs]
    assert "admin.auth_invalid_token" in events
    assert "admin.policy_reloaded" in events
    rendered = repr(admin_logs)
    assert "a-wrong-secret" not in rendered
    assert ADMIN_TOKEN not in rendered


async def test_the_openapi_documents_the_admin_security_scheme(
    anonymous: httpx.AsyncClient,
) -> None:
    spec = (await anonymous.get("/openapi.json")).json()

    assert spec["components"]["securitySchemes"]["AdminToken"]["scheme"] == "bearer"
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/v1/admin"):
            continue
        for operation in operations.values():
            assert operation["security"] == [{"AdminToken": []}]
            assert {"401", "503"} <= operation["responses"].keys()
            assert operation["summary"]
