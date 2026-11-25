"""Route-level tests for the PyPI mirror (upstream mocked with respx)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from hashlib import sha256

import httpx
import pytest
import respx

from supply_shield.proxy.download import UpstreamIntegrityError
from supply_shield.proxy.pypi.accept import JSON_MEDIA_TYPE

PyPIClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]

SIMPLE_URL = "https://pypi.org/simple/django/"
FILE_URL = "https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl"
FILE_BODY = b"intact-wheel-bytes" * 8
FILE_SHA = sha256(FILE_BODY).hexdigest()
PROXY_FILE = f"http://testserver/pypi/packages/sha256/{FILE_SHA}/ab/cd/Django-5.0-py3-none-any.whl"
HASHED_PATH = f"/pypi/packages/sha256/{FILE_SHA}/ab/cd/Django-5.0-py3-none-any.whl"

SIMPLE_JSON = {
    "meta": {"api-version": "1.1"},
    "name": "django",
    "files": [
        {
            "filename": "Django-5.0-py3-none-any.whl",
            "url": FILE_URL,
            "hashes": {"sha256": FILE_SHA},
            "requires-python": ">=3.10",
        }
    ],
}

JSON_HEADERS = {"content-type": "application/vnd.pypi.simple.v1+json"}
HTML_HEADERS = {"content-type": "text/html"}

SIMPLE_HTML = (
    '<html><body><a href="'
    f"{FILE_URL}#sha256={FILE_SHA}"
    '">Django-5.0-py3-none-any.whl</a></body></html>'
)


@respx.mock
async def test_simple_index_serves_json_when_requested(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(200, json=SIMPLE_JSON, headers=JSON_HEADERS)

    response = await client.get("/pypi/simple/django/", headers={"Accept": JSON_MEDIA_TYPE})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(JSON_MEDIA_TYPE)
    payload = response.json()
    assert payload["name"] == "django"
    file = payload["files"][0]
    assert file["url"] == f"{PROXY_FILE}#sha256={FILE_SHA}"
    assert file["hashes"] == {"sha256": FILE_SHA}


@respx.mock
async def test_simple_index_serves_html_by_default(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(200, text=SIMPLE_HTML, headers=HTML_HEADERS)

    response = await client.get("/pypi/simple/django/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert PROXY_FILE in response.text


@respx.mock
async def test_simple_index_redirects_to_trailing_slash(client: httpx.AsyncClient) -> None:
    response = await client.get("/pypi/simple/django", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/pypi/simple/django/"


@respx.mock
async def test_simple_index_returns_404_when_upstream_missing(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(404)
    response = await client.get("/pypi/simple/django/")
    assert response.status_code == 404


@respx.mock
async def test_simple_index_returns_502_on_upstream_error(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(500)
    response = await client.get("/pypi/simple/django/")
    assert response.status_code == 502


@respx.mock
async def test_download_streams_intact_bytes(client: httpx.AsyncClient) -> None:
    respx.get(FILE_URL).respond(200, content=FILE_BODY)
    response = await client.get(HASHED_PATH)
    assert response.status_code == 200
    assert response.content == FILE_BODY


@respx.mock
async def test_download_without_hash_streams_unverified(client: httpx.AsyncClient) -> None:
    respx.get(FILE_URL).respond(200, content=FILE_BODY)
    response = await client.get("/pypi/packages/ab/cd/Django-5.0-py3-none-any.whl")
    assert response.status_code == 200
    assert response.content == FILE_BODY


@respx.mock
async def test_download_aborts_when_hash_mismatches(client: httpx.AsyncClient) -> None:
    respx.get(FILE_URL).respond(200, content=b"tampered")
    with pytest.raises(UpstreamIntegrityError):
        await client.get(HASHED_PATH)


@respx.mock
async def test_metadata_file_is_not_checked_against_the_wheel_hash(
    client: httpx.AsyncClient,
) -> None:
    """pip pide ``<url sin fragmento>.metadata``: su contenido no tiene el hash del wheel."""
    respx.get(f"{FILE_URL}.metadata").respond(200, content=b"Metadata-Version: 2.1\n")
    response = await client.get(f"{HASHED_PATH}.metadata")
    assert response.status_code == 200
    assert response.content == b"Metadata-Version: 2.1\n"


@respx.mock
async def test_download_maps_upstream_404_to_502(client: httpx.AsyncClient) -> None:
    respx.get(FILE_URL).respond(404)
    response = await client.get("/pypi/packages/ab/cd/Django-5.0-py3-none-any.whl")
    assert response.status_code == 502


@respx.mock
async def test_download_rejects_bad_hash_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/pypi/packages/sha256/nothex/ab/cd/Django-5.0-py3-none-any.whl")
    assert response.status_code == 400
    assert not respx.calls


@respx.mock
async def test_download_rejects_path_traversal(client: httpx.AsyncClient) -> None:
    response = await client.get("/pypi/packages/ab/../../etc/passwd")
    assert response.status_code in (400, 404)
    assert not respx.calls


@respx.mock
async def test_routes_require_a_project_token(pypi_client: PyPIClientFactory) -> None:
    async with pypi_client(token=None) as anonymous:
        index = await anonymous.get("/pypi/simple/django/")
        file = await anonymous.get("/pypi/packages/ab/cd/x.whl")
    assert index.status_code == 401
    assert file.status_code == 401
    assert not respx.calls
