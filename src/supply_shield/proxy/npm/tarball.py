"""Resolución y descarga verificada de tarballs npm (sobre D1-1)."""

from typing import NamedTuple

import httpx
import structlog

from supply_shield.config import Settings
from supply_shield.proxy.download import UpstreamDownload, open_upstream_download
from supply_shield.proxy.npm.packument import (
    ExpectedDigest,
    expected_digest,
    fetch_packument,
    parse_packument,
    versions_of,
)
from supply_shield.proxy.upstream import UpstreamRequestError

# Lo mismo que envía npm: el formato abreviado basta para resolver un tarball y es
# mucho más chico que el completo; el resto cubre registros que no lo ofrecen.
ABBREVIATED_ACCEPT = "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8, */*"

_TARBALL_PATH_SUFFIX = "/-/"
_OK_STATUS = 200
_NOT_FOUND_STATUS = 404
_log = structlog.get_logger(__name__)


class TarballNotFoundError(LookupError):
    """El paquete no existe en el upstream o ninguna versión publica ese tarball."""


class ResolvedTarball(NamedTuple):
    """URL upstream de un tarball y el digest con el que se verifica."""

    url: str
    digest: ExpectedDigest


async def resolve_tarball(
    client: httpx.AsyncClient,
    package: str,
    filename: str,
    settings: Settings,
) -> ResolvedTarball:
    """Resuelve la URL original y el digest esperado de un tarball.

    Obtiene el packument abreviado del upstream y busca la versión cuyo
    ``dist.tarball`` termina en ``/-/{filename}``. Lanza :class:`TarballNotFoundError`
    si el paquete o el tarball no existen, :class:`UpstreamRequestError` si el upstream
    responde con otro error, ``InvalidPackumentError`` si el packument no tiene la
    forma esperada e ``IntegrityUnavailableError`` si la versión no publica un digest.
    """
    response = await fetch_packument(client, package, settings, accept=ABBREVIATED_ACCEPT)
    if response.status_code == _NOT_FOUND_STATUS:
        raise TarballNotFoundError(f"package {package} not found upstream")
    if response.status_code != _OK_STATUS:
        raise UpstreamRequestError(
            f"upstream packument for {package} returned {response.status_code}",
        )

    packument = parse_packument(response)
    suffix = f"{_TARBALL_PATH_SUFFIX}{filename}"
    for version in versions_of(packument).values():
        dist = version.get("dist") if isinstance(version, dict) else None
        if not isinstance(dist, dict):
            continue
        tarball_url = dist.get("tarball")
        if not isinstance(tarball_url, str) or not tarball_url.endswith(suffix):
            continue
        digest = expected_digest(dist)
        if digest.algorithm != "sha512":
            _log.warning(
                "npm.tarball.legacy_digest",
                package=package,
                filename=filename,
                algorithm=digest.algorithm,
            )
        _log.info("npm.tarball.resolved", package=package, filename=filename, url=tarball_url)
        return ResolvedTarball(tarball_url, digest)

    raise TarballNotFoundError(f"no tarball matching {filename} found in {package}")


async def open_tarball(
    client: httpx.AsyncClient,
    tarball: ResolvedTarball,
    settings: Settings,
) -> UpstreamDownload:
    """Abre la descarga del tarball y la valida antes de responder al cliente (D1-1)."""
    _log.info("npm.tarball.stream_start", url=tarball.url)
    return await open_upstream_download(
        client,
        tarball.url,
        tarball.digest.hex,
        settings,
        algorithm=tarball.digest.algorithm,
    )
