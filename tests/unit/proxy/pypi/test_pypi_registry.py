"""Unit tests for the PyPI registry client (upstream mocked with respx)."""

import httpx
import pytest
import respx

from supply_shield.config import Settings
from supply_shield.proxy.pypi.registry import (
    InvalidProjectNameError,
    ProjectNotFoundError,
    PyPIRegistry,
)
from supply_shield.proxy.upstream import UpstreamRequestError, build_upstream_client

SIMPLE_URL = "https://pypi.org/simple/django/"
JSON_URL = "https://pypi.org/pypi/django/json"

SIMPLE_JSON = {
    "meta": {"api-version": "1.1"},
    "name": "django",
    "files": [
        {
            "filename": "Django-5.0-py3-none-any.whl",
            "url": "https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl",
            "hashes": {"sha256": "aaa1"},
        }
    ],
}

SIMPLE_HTML = (
    '<html><body><a href="https://files.pythonhosted.org/packages/ab/cd/'
    'Django-5.0-py3-none-any.whl#sha256=aaa1">Django-5.0-py3-none-any.whl</a></body></html>'
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "upstream_max_retries": 0,
        "upstream_backoff_factor": 0.001,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@respx.mock
async def test_fetch_index_parses_json_and_normalizes_name() -> None:
    respx.get(SIMPLE_URL).respond(
        200,
        json=SIMPLE_JSON,
        headers={"content-type": "application/vnd.pypi.simple.v1+json"},
    )
    settings = _settings()
    async with build_upstream_client(settings) as client:
        project = await PyPIRegistry(client, settings).fetch_index("Django")
    assert project.name == "django"
    assert project.files[0].sha256 == "aaa1"
    assert respx.calls.last.request.headers["accept"].startswith("application/vnd.pypi.simple")


@respx.mock
async def test_fetch_index_parses_html_fallback() -> None:
    respx.get(SIMPLE_URL).respond(200, text=SIMPLE_HTML, headers={"content-type": "text/html"})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        project = await PyPIRegistry(client, settings).fetch_index("django")
    assert project.files[0].filename == "Django-5.0-py3-none-any.whl"


@respx.mock
async def test_fetch_index_raises_when_missing() -> None:
    respx.get(SIMPLE_URL).respond(404)
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(ProjectNotFoundError):
            await PyPIRegistry(client, settings).fetch_index("django")


@respx.mock
async def test_fetch_index_raises_on_server_error() -> None:
    respx.get(SIMPLE_URL).respond(500)
    settings = _settings()
    async with build_upstream_client(settings) as client:
        with pytest.raises(UpstreamRequestError):
            await PyPIRegistry(client, settings).fetch_index("django")


@respx.mock
async def test_fetch_metadata_builds_upstream_metadata() -> None:
    respx.get(JSON_URL).respond(
        200,
        json={
            "info": {"version": "5.0", "author": "Django Software Foundation"},
            "releases": {"5.0": [{"upload_time_iso_8601": "2023-12-04T09:00:00Z"}]},
        },
    )
    settings = _settings()
    async with build_upstream_client(settings) as client:
        metadata = await PyPIRegistry(client, settings).fetch_metadata("Django")
    assert metadata.package.name == "django"
    assert metadata.package.version == "5.0"
    assert metadata.versions[0].version == "5.0"
    assert metadata.maintainers[0].name == "Django Software Foundation"


@respx.mock
async def test_registry_exposes_client_and_settings() -> None:
    settings = _settings()
    async with build_upstream_client(settings) as client:
        registry = PyPIRegistry(client, settings)
        assert isinstance(registry.client, httpx.AsyncClient)
        assert registry.settings is settings
        assert registry.artifact_settings.upstream_max_response_bytes == (
            settings.pypi_max_artifact_bytes
        )
        assert registry.artifact_settings.upstream_hosts == settings.upstream_hosts


@respx.mock
async def test_fetch_index_keeps_pep700_versions_from_json() -> None:
    payload = {**SIMPLE_JSON, "versions": ["4.2", "5.0"]}
    respx.get(SIMPLE_URL).respond(
        200, json=payload, headers={"content-type": "application/vnd.pypi.simple.v1+json"}
    )
    settings = _settings()
    async with build_upstream_client(settings) as client:
        project = await PyPIRegistry(client, settings).fetch_index("django")
    assert project.versions == ("4.2", "5.0")


@respx.mock
async def test_fetch_index_from_html_has_no_versions() -> None:
    respx.get(SIMPLE_URL).respond(200, text=SIMPLE_HTML, headers={"content-type": "text/html"})
    settings = _settings()
    async with build_upstream_client(settings) as client:
        project = await PyPIRegistry(client, settings).fetch_index("django")
    assert project.versions is None


@pytest.mark.parametrize("name", ["foo_", "-x", "ñandu", ""])
@respx.mock
async def test_invalid_name_raises_without_calling_upstream(name: str) -> None:
    settings = _settings()
    async with build_upstream_client(settings) as client:
        registry = PyPIRegistry(client, settings)
        with pytest.raises(InvalidProjectNameError):
            await registry.fetch_index(name)
        with pytest.raises(InvalidProjectNameError):
            await registry.fetch_metadata(name)
    assert not respx.calls
