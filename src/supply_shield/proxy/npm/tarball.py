"""Descarga de tarballs npm con verificación de integridad vía D1-1."""

from collections.abc import AsyncIterator
from typing import Any

import structlog

from supply_shield.config import Settings
from supply_shield.proxy.download import stream_upstream
from supply_shield.proxy.npm.packument import fetch_packument, integrity_to_hex
from supply_shield.proxy.upstream import UpstreamRequestError, build_upstream_client

_TARBALL_PATH_SUFFIX = "/-/"
_OK_STATUS = 200
_log = structlog.get_logger(__name__)


async def resolve_tarball(
    package: str,
    filename: str,
    settings: Settings,
) -> tuple[str, str]:
    """Resuelve la URL original y el digest hex esperado de un tarball.

    Obtiene el packument del upstream y busca la versión cuyo ``dist.tarball``
    termina en ``/-/{filename}``. Devuelve ``(tarball_url, expected_hex)`` o lanza
    :class:`UpstreamRequestError` si no encuentra el paquete o el tarball.
    """
    async with build_upstream_client(settings) as client:
        packument_response = await fetch_packument(client, package, settings)
        if packument_response.status_code != _OK_STATUS:
            raise UpstreamRequestError(
                f"upstream packument for {package} returned {packument_response.status_code}",
            )

        packument: dict[str, Any] = packument_response.json()
        suffix = f"{_TARBALL_PATH_SUFFIX}{filename}"
        for version in packument.get("versions", {}).values():
            dist = version.get("dist") if isinstance(version, dict) else None
            if not isinstance(dist, dict):
                continue
            tarball_url = dist.get("tarball", "")
            integrity = dist.get("integrity", "")
            if isinstance(tarball_url, str) and tarball_url.endswith(suffix) and integrity:
                _log.info(
                    "npm.tarball.resolved",
                    package=package,
                    filename=filename,
                    url=tarball_url,
                )
                return tarball_url, integrity_to_hex(integrity)

    raise UpstreamRequestError(f"no tarball matching {filename} found in {package}")


async def stream_tarball(
    tarball_url: str,
    expected_hex: str,
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Streamea un tarball desde el upstream verificando su SHA-512 (D1-1)."""
    _log.info("npm.tarball.stream_start", url=tarball_url)
    async with build_upstream_client(settings) as client:
        async for chunk in stream_upstream(client, tarball_url, expected_hex, settings):
            yield chunk
