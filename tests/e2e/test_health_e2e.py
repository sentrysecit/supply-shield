import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_healthz_via_client(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == httpx.codes.OK
    assert response.json() == {"status": "ok"}


async def test_readyz_via_client(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == httpx.codes.OK
    assert response.json() == {"status": "ok"}


async def test_unknown_path_via_client(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == httpx.codes.NOT_FOUND


def test_healthz_via_live_server(live_server: str) -> None:
    with httpx.Client(base_url=live_server) as client:
        response = client.get("/healthz")
    assert response.status_code == httpx.codes.OK
    assert response.json()["status"] == "ok"


def test_readyz_via_live_server(live_server: str) -> None:
    with httpx.Client(base_url=live_server) as client:
        response = client.get("/readyz")
    assert response.status_code == httpx.codes.OK
