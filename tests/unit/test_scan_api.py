"""Pruebas del endpoint POST /api/v1/scan (D5-6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from supply_shield.config import Settings
from supply_shield.core import Finding, PackageRef, RequestContext, Severity
from supply_shield.main import create_app
from supply_shield.policy import Policy, PolicyHolder, default_source

FIXTURES = Path(__file__).parent / "fixtures" / "lockfiles"
REQUIREMENTS = (FIXTURES / "requirements.txt").read_bytes()
TOKEN = "test-token"  # noqa: S105
PROJECT = "test-project"


class _FakeStore:
    async def verify(self, token: str) -> str | None:
        return PROJECT if token == TOKEN else None


class _CleanAnalyzer:
    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        return []


class _BlockingAnalyzer:
    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        if package.name == "requests":
            return [
                Finding(
                    rule="dependency_confusion",
                    severity=Severity.CRITICAL,
                    source="test",
                    detail="nombre interno pedido al registro público",
                    package=package,
                )
            ]
        return []


@pytest.fixture
async def scan_client() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    """App con token de prueba y analizadores falsos; sin base de datos."""
    app = create_app(Settings(_env_file=None))
    app.state.token_store = _FakeStore()
    app.state.analyzers = (_CleanAnalyzer(),)
    settings = Settings(_env_file=None)
    app.state.policy_holder = PolicyHolder(Policy(), default_source(settings.policy_file))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, app


async def _post(
    client: httpx.AsyncClient, body: bytes, filename: str = "requirements.txt"
) -> httpx.Response:
    return await client.post(
        "/api/v1/scan",
        params={"filename": filename},
        content=body,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


async def test_scan_requires_auth(scan_client: tuple[httpx.AsyncClient, Any]) -> None:
    """Sin token responde 401."""
    client, _ = scan_client
    response = await client.post(
        "/api/v1/scan", params={"filename": "requirements.txt"}, content=REQUIREMENTS
    )
    assert response.status_code == 401


async def test_scan_rejects_unsupported_lockfile(
    scan_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """Un nombre de lockfile desconocido responde 400 con el error de parseo."""
    client, _ = scan_client
    response = await _post(client, b"not a lockfile", filename="evil.txt")
    assert response.status_code == 400
    assert "unsupported lockfile format" in response.json()["detail"]


async def test_scan_clean_lockfile(scan_client: tuple[httpx.AsyncClient, Any]) -> None:
    """Un lockfile limpio devuelve 200 con SBOM, decisiones y blocked=false."""
    client, _ = scan_client
    response = await _post(client, REQUIREMENTS)
    assert response.status_code == 200
    data = response.json()
    assert data["blocked"] is False
    assert data["stored"] is False  # sin motor de BD inicializado
    assert len(data["results"]) == 2  # requests + certifi del fixture
    purls = {result["package"]["name"] for result in data["results"]}
    assert purls == {"requests", "certifi"}
    assert data["sbom"]["bomFormat"] == "CycloneDX"
    component_names = {c["name"] for c in data["sbom"]["components"]}
    assert {"requests", "certifi"} <= component_names


async def test_scan_blocks_flagged_package(scan_client: tuple[httpx.AsyncClient, Any]) -> None:
    """Un hallazgo bloqueante marca el paquete BLOCK y blocked=true."""
    client, app = scan_client
    app.state.analyzers = (_BlockingAnalyzer(),)
    response = await _post(client, REQUIREMENTS)
    assert response.status_code == 200
    data = response.json()
    assert data["blocked"] is True
    by_name = {result["package"]["name"]: result for result in data["results"]}
    assert by_name["requests"]["decision"]["action"] == "block"
    assert by_name["certifi"]["decision"]["action"] == "allow"
    vulnerabilities = data["sbom"].get("vulnerabilities", [])
    assert any(v.get("id") or v.get("bomRef") for v in vulnerabilities) or vulnerabilities
