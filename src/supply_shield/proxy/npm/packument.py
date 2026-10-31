"""Obtención y parseo de packuments del registro npm upstream."""

from collections.abc import Mapping
from urllib.parse import unquote

import httpx

from supply_shield.config import Settings
from supply_shield.proxy.upstream import upstream_get

_NPM_REGISTRY = "https://registry.npmjs.org"


def decode_package_name(encoded: str) -> str:
    """Decodifica un nombre de paquete que puede llegar URL-encoded (scopes)."""
    return unquote(encoded)


async def fetch_packument(
    client: httpx.AsyncClient,
    package: str,
    settings: Settings,
    accept: str | None = None,
) -> httpx.Response:
    """Obtiene el packument de ``package`` desde el registro npm upstream.

    Se reenvía la cabecera ``accept`` para que el upstream devuelva el formato
    completo o el abreviado (`vnd.npm.install-v1+json`) según lo que pida el cliente.
    """
    url = f"{_NPM_REGISTRY}/{package}"
    headers: Mapping[str, str] | None = {"Accept": accept} if accept else None
    return await upstream_get(client, url, settings, headers=headers)
