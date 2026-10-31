"""Pruebas de las sondas de vida y de preparación."""

import httpx
import pytest


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_probe_reports_ok(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == httpx.codes.OK
    assert response.json() == {"status": "ok"}
