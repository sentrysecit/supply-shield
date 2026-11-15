"""Route-level tests for the PyPI mirror (upstream mocked with respx)."""

from hashlib import sha256

import httpx
import respx

from supply_shield.proxy.pypi.accept import JSON_MEDIA_TYPE

SIMPLE_URL = "https://pypi.org/simple/django/"
FILE_URL = "https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl"
FILE_BODY = b"intact-wheel-bytes" * 8
FILE_SHA = sha256(FILE_BODY).hexdigest()

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

    response = await client.get("/simple/django/", headers={"Accept": JSON_MEDIA_TYPE})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(JSON_MEDIA_TYPE)
    payload = response.json()
    assert payload["name"] == "django"
    file = payload["files"][0]
    assert file["url"] == (
        f"http://testserver/packages/ab/cd/Django-5.0-py3-none-any.whl"
        f"?sha256={FILE_SHA}#sha256={FILE_SHA}"
    )
    assert file["hashes"] == {"sha256": FILE_SHA}


@respx.mock
async def test_simple_index_serves_html_by_default(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(200, text=SIMPLE_HTML, headers=HTML_HEADERS)

    response = await client.get("/simple/django/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "http://testserver/packages/ab/cd/Django-5.0-py3-none-any.whl" in response.text


@respx.mock
async def test_simple_index_redirects_to_trailing_slash(client: httpx.AsyncClient) -> None:
    response = await client.get("/simple/django", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/simple/django/"


@respx.mock
async def test_simple_index_returns_404_when_upstream_missing(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(404)
    response = await client.get("/simple/django/")
    assert response.status_code == 404


@respx.mock
async def test_simple_index_returns_502_on_upstream_error(client: httpx.AsyncClient) -> None:
    respx.get(SIMPLE_URL).respond(500)
    response = await client.get("/simple/django/")
    assert response.status_code == 502


@respx.mock
async def test_download_streams_intact_bytes(client: httpx.AsyncClient) -> None:
    respx.get(FILE_URL).respond(200, content=FILE_BODY)
    response = await client.get(f"/packages/ab/cd/Django-5.0-py3-none-any.whl?sha256={FILE_SHA}")
    assert response.status_code == 200
    assert response.content == FILE_BODY


@respx.mock
async def test_download_rejects_bad_hash_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/packages/ab/cd/Django-5.0-py3-none-any.whl?sha256=nothex")
    assert response.status_code == 400
    assert not respx.calls
