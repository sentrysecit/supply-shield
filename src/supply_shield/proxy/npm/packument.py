"""Obtención y parseo de packuments del registro npm upstream."""

import base64
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import httpx
import structlog

from supply_shield.config import Settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.maintainer import Maintainer
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.upstream_metadata import UpstreamMetadata
from supply_shield.proxy.upstream import upstream_get

_NPM_REGISTRY = "https://registry.npmjs.org"
_NPM_TARBALL_PREFIX = "sha512-"
_log = structlog.get_logger(__name__)

_UNSAFE_SEQUENCES = ("..", "\x00")


def decode_package_name(encoded: str) -> str:
    """Decodifica un nombre de paquete que puede llegar URL-encoded (scopes)."""
    return unquote(encoded)


def validate_package_name(name: str) -> None:
    """Rechaza nombres de paquete con secuencias peligrosas antes de reenviar al upstream.

    Bloquea path traversal (`..`) y bytes nulos para evitar que nombres adversariales
    lleguen al upstream o exploten errores de parseo aguas abajo.
    """
    for seq in _UNSAFE_SEQUENCES:
        if seq in name:
            raise ValueError(f"secuencia no permitida {seq!r} en nombre de paquete {name!r}")


def _rewrite_tarball_url(original: str, proxy_base_url: str) -> str:
    """Reescribe una URL de tarball del upstream para que apunte al proxy."""
    parsed = httpx.URL(original)
    return f"{proxy_base_url}/npm{parsed.path}"


def rewrite_tarballs(packument: dict[str, Any], proxy_base_url: str) -> dict[str, Any]:
    """Reescribe cada ``dist.tarball`` del packument para que apunte al proxy.

    Sin esto, el cliente descargaría los tarballs directamente del upstream,
    bypassando el firewall. Ver ADR 0002 y ADR 0006.
    """
    name = packument.get("name", "")
    rewritten = 0
    for version in packument.get("versions", {}).values():
        dist = version.get("dist") if isinstance(version, dict) else None
        if isinstance(dist, dict) and "tarball" in dist:
            original = dist["tarball"]
            dist["tarball"] = _rewrite_tarball_url(original, proxy_base_url)
            rewritten += 1
    _log.debug("npm.packument.tarballs_rewritten", package=name, count=rewritten)
    return packument


def integrity_to_hex(integrity: str) -> str:
    """Convierte el ``integrity`` de npm (``sha512-<base64>``) a digest hex."""
    if integrity.startswith(_NPM_TARBALL_PREFIX):
        raw = base64.b64decode(integrity[len(_NPM_TARBALL_PREFIX) :])
        return raw.hex()
    return integrity.lower()


def _parse_time(value: Any) -> datetime | None:
    """Parsea una marca de tiempo ISO 8601 del packument; ``None`` si no es válida."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def to_upstream_metadata(packument: dict[str, Any]) -> UpstreamMetadata:
    """Convierte un packument de npm al modelo común ``UpstreamMetadata``.

    Extrae las versiones con su fecha de publicación (de ``time``), los scripts
    de la versión ``latest`` y los mantenedores. Los detectores consumen este
    modelo sin conocer el formato específico de npm.
    """
    name = packument.get("name", "")
    versions_data = packument.get("versions", {})
    time_data = packument.get("time", {}) if isinstance(packument.get("time"), dict) else {}
    dist_tags = (
        packument.get("dist-tags", {}) if isinstance(packument.get("dist-tags"), dict) else {}
    )
    latest_version = dist_tags.get("latest")

    releases: list[PackageRelease] = []
    for version_key, version_info in versions_data.items():
        if not isinstance(version_info, dict):
            continue
        published_at = _parse_time(time_data.get(version_key))
        releases.append(PackageRelease(version=version_key, published_at=published_at))

    scripts: dict[str, str] = {}
    if isinstance(latest_version, str) and latest_version in versions_data:
        latest_info = versions_data[latest_version]
        raw_scripts = latest_info.get("scripts", {}) if isinstance(latest_info, dict) else {}
        if isinstance(raw_scripts, dict):
            scripts = {
                key: value
                for key, value in raw_scripts.items()
                if isinstance(key, str) and isinstance(value, str)
            }

    maintainers: list[Maintainer] = []
    for entry in packument.get("maintainers", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            email = entry.get("email")
            maintainers.append(
                Maintainer(name=entry["name"], email=email if isinstance(email, str) else None),
            )

    return UpstreamMetadata(
        package=PackageRef(ecosystem=Ecosystem.NPM, name=name),
        versions=tuple(releases),
        scripts=scripts,
        maintainers=tuple(maintainers),
    )


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
