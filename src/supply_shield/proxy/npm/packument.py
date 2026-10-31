"""Obtención y parseo de packuments del registro npm upstream."""

import base64
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

import httpx

from supply_shield.config import Settings
from supply_shield.proxy.upstream import upstream_get

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_TARBALL_PREFIX = "sha512-"


def decode_package_name(encoded: str) -> str:
    """Decodifica un nombre de paquete que puede llegar URL-encoded (scopes)."""
    return unquote(encoded)


def _rewrite_tarball_url(original: str, proxy_base_url: str) -> str:
    """Reescribe una URL de tarball del upstream para que apunte al proxy."""
    parsed = httpx.URL(original)
    return f"{proxy_base_url}/npm{parsed.path}"


def rewrite_tarballs(packument: dict[str, Any], proxy_base_url: str) -> dict[str, Any]:
    """Reescribe cada ``dist.tarball`` del packument para que apunte al proxy.

    Sin esto, el cliente descargaría los tarballs directamente del upstream,
    bypassando el firewall. Ver ADR 0002 y ADR 0006.
    """
    for version in packument.get("versions", {}).values():
        dist = version.get("dist") if isinstance(version, dict) else None
        if isinstance(dist, dict) and "tarball" in dist:
            dist["tarball"] = _rewrite_tarball_url(dist["tarball"], proxy_base_url)
    return packument


def integrity_to_hex(integrity: str) -> str:
    """Convierte el ``integrity`` de npm (``sha512-<base64>``) a digest hex."""
    if integrity.startswith(_NPM_TARBALL_PREFIX):
        raw = base64.b64decode(integrity[len(_NPM_TARBALL_PREFIX) :])
        return raw.hex()
    return integrity.lower()


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
