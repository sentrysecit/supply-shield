"""Cliente del espejo de PyPI: consulta el upstream y arma la respuesta del proxy.

Todo el tráfico sale por el cliente HTTP compartido (D1-1), que aplica la
allowlist de hosts y los reintentos. Desde aquí solo se eligen los endpoints de
PyPI y se delega el parseo a los módulos de índice y metadata.
"""

import httpx

from supply_shield.config import Settings
from supply_shield.core import Ecosystem, UpstreamMetadata
from supply_shield.core.naming import normalize_package_name
from supply_shield.proxy.pypi.metadata import parse_pypi_metadata
from supply_shield.proxy.pypi.models import DistributionFile, SimpleProject
from supply_shield.proxy.pypi.parse import parse_simple_html, parse_simple_json
from supply_shield.proxy.upstream import UpstreamRequestError, upstream_get

PYPI_SIMPLE_BASE = "https://pypi.org/simple"
PYPI_JSON_BASE = "https://pypi.org/pypi"

_NOT_FOUND = 404
_ERROR_STATUS_LOWER = 400
_SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json, text/html;q=0.1"


class ProjectNotFoundError(LookupError):
    """El registro upstream no conoce el paquete solicitado (404)."""


class PyPIRegistry:
    """Acceso de solo lectura al índice simple y a la JSON API de PyPI."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def client(self) -> httpx.AsyncClient:
        """Cliente HTTP compartido, para que el router streamee archivos con él."""
        return self._client

    @property
    def settings(self) -> Settings:
        """Ajustes de ejecución, incluidos timeouts y límite de tamaño."""
        return self._settings

    async def fetch_index(self, name: str) -> SimpleProject:
        """Descarga y parsea el índice simple de ``name`` desde el upstream."""
        normalized = normalize_package_name(Ecosystem.PYPI, name)
        url = f"{PYPI_SIMPLE_BASE}/{normalized}/"
        response = await upstream_get(
            self._client, url, self._settings, headers={"Accept": _SIMPLE_ACCEPT}
        )
        self._raise_for_status(response, url)
        files = self._parse_index(response)
        return SimpleProject(name=normalized, files=files)

    async def fetch_metadata(self, name: str) -> UpstreamMetadata:
        """Descarga la JSON API de PyPI y la convierte en ``UpstreamMetadata``."""
        normalized = normalize_package_name(Ecosystem.PYPI, name)
        url = f"{PYPI_JSON_BASE}/{normalized}/json"
        response = await upstream_get(self._client, url, self._settings)
        self._raise_for_status(response, url)
        return parse_pypi_metadata(normalized, response.json())

    @staticmethod
    def _parse_index(response: httpx.Response) -> tuple[DistributionFile, ...]:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            return parse_simple_json(response.json())
        return parse_simple_html(response.text)

    @staticmethod
    def _raise_for_status(response: httpx.Response, url: str) -> None:
        if response.status_code == _NOT_FOUND:
            raise ProjectNotFoundError(url)
        if response.status_code >= _ERROR_STATUS_LOWER:
            raise UpstreamRequestError(f"upstream {url} returned status {response.status_code}")
