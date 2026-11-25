"""Obtención, validación y parseo de packuments del registro npm upstream."""

import base64
import binascii
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, NamedTuple
from urllib.parse import quote

import httpx
import structlog

from supply_shield.config import Settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.maintainer import Maintainer
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.upstream_metadata import UpstreamMetadata
from supply_shield.proxy.download import DigestAlgorithm
from supply_shield.proxy.upstream import upstream_get

_NPM_REGISTRY = "https://registry.npmjs.org"
_log = structlog.get_logger(__name__)

# Un nombre npm, con scope opcional, solo usa caracteres que ``encodeURIComponent``
# deja igual y no empieza por punto ni guion bajo. Se aceptan mayúsculas y ~'!()*
# porque hay paquetes antiguos que los usan (p. ej. ``JSONStream``). Nada de esto
# puede cambiar la ruta o la query de la URL upstream: ni ``/`` fuera del scope, ni
# ``?``, ``#``, ``%``, espacios o caracteres de control.
_NAME_PART = r"[A-Za-z0-9~!'()*-][A-Za-z0-9._~!'()*-]*"
_NPM_NAME = re.compile(rf"(?:@{_NAME_PART}/)?{_NAME_PART}")
_MAX_NAME_LENGTH = 214

# Algoritmos del ``integrity`` (SSRI) que sabemos verificar, con el tamaño de su digest.
_SRI_ALGORITHMS: Mapping[str, DigestAlgorithm] = {"sha512": "sha512", "sha1": "sha1"}
_DIGEST_SIZES: Mapping[DigestAlgorithm, int] = {"sha512": 64, "sha1": 20}
_PREFERENCE: tuple[DigestAlgorithm, ...] = ("sha512", "sha1")
_SHA1_HEX = re.compile(r"[0-9a-fA-F]{40}")


class InvalidPackumentError(ValueError):
    """El upstream devolvió un packument que no tiene la forma esperada."""


class IntegrityUnavailableError(ValueError):
    """La versión no publica un digest utilizable para verificar su tarball."""


class ExpectedDigest(NamedTuple):
    """Digest esperado de un tarball: algoritmo y valor en hexadecimal."""

    algorithm: DigestAlgorithm
    hex: str


def validate_package_name(name: str) -> None:
    """Rechaza todo lo que no tenga la forma de un nombre de paquete npm.

    Se valida contra una forma estricta en lugar de buscar secuencias peligrosas:
    así ningún nombre puede añadir segmentos, una query o un fragmento a la URL
    upstream, aunque llegue codificado. Lanza ``ValueError`` si el nombre no es válido.
    """
    if len(name) > _MAX_NAME_LENGTH or _NPM_NAME.fullmatch(name) is None:
        raise ValueError(f"invalid npm package name {name!r}")


def packument_url(package: str) -> str:
    """URL del packument de ``package`` en el registro upstream, con el nombre codificado."""
    return f"{_NPM_REGISTRY}/{quote(package, safe='@/')}"


def parse_packument(response: httpx.Response) -> dict[str, Any]:
    """Parsea el cuerpo de un packument y comprueba su forma básica.

    Lanza :class:`InvalidPackumentError` si el cuerpo no es un objeto JSON o si
    ``versions`` existe pero no es un objeto. Un packument sin ``versions`` es válido:
    es lo que devuelve el registro para un paquete despublicado.
    """
    try:
        data = response.json()
    except ValueError as error:
        raise InvalidPackumentError("upstream packument is not valid JSON") from error
    if not isinstance(data, dict):
        raise InvalidPackumentError("upstream packument is not a JSON object")
    if not isinstance(data.get("versions", {}), dict):
        raise InvalidPackumentError("upstream packument has a non-object 'versions'")
    return data


def versions_of(packument: Mapping[str, Any]) -> dict[str, Any]:
    """Devuelve el objeto ``versions`` del packument, o uno vacío si falta o no es válido."""
    versions = packument.get("versions", {})
    return versions if isinstance(versions, dict) else {}


def _rewrite_tarball_url(original: object, proxy_base_url: str) -> str:
    """Reescribe una URL de tarball del upstream para que apunte al proxy."""
    if not isinstance(original, str):
        raise InvalidPackumentError("dist.tarball is not a string")
    try:
        parsed = httpx.URL(original)
    except httpx.InvalidURL as error:
        raise InvalidPackumentError("dist.tarball is not a valid URL") from error
    return f"{proxy_base_url}/npm{parsed.path}"


def rewrite_tarballs(packument: dict[str, Any], proxy_base_url: str) -> dict[str, Any]:
    """Reescribe cada ``dist.tarball`` del packument para que apunte al proxy.

    Sin esto, el cliente descargaría los tarballs directamente del upstream,
    bypassando el firewall. Ver ADR 0002 y ADR 0013. Si algún ``dist.tarball`` no se
    puede reescribir se lanza :class:`InvalidPackumentError`: es preferible no servir
    el packument a dejar salir una URL del upstream.
    """
    versions = packument.get("versions", {})
    if not isinstance(versions, dict):
        raise InvalidPackumentError("upstream packument has a non-object 'versions'")
    rewritten = 0
    for version in versions.values():
        dist = version.get("dist") if isinstance(version, dict) else None
        if isinstance(dist, dict) and "tarball" in dist:
            dist["tarball"] = _rewrite_tarball_url(dist["tarball"], proxy_base_url)
            rewritten += 1
    _log.debug(
        "npm.packument.tarballs_rewritten", package=packument.get("name", ""), count=rewritten
    )
    return packument


def expected_digest(dist: Mapping[str, Any]) -> ExpectedDigest:
    """Elige el digest más fuerte que publica ``dist`` para verificar su tarball.

    El ``integrity`` es una cadena SSRI que puede traer varias entradas separadas
    por espacios y en cualquier orden. Se prefiere ``sha512``; si no hay, ``sha1``
    del propio ``integrity`` o, en versiones antiguas sin ``integrity``, el
    ``shasum`` en hexadecimal (ADR 0014). Lanza :class:`IntegrityUnavailableError`
    si no hay ninguno o si una entrada de un algoritmo conocido está mal formada.
    """
    integrity = dist.get("integrity")
    entries = _parse_sri(integrity) if isinstance(integrity, str) else {}
    for algorithm in _PREFERENCE:
        digest = entries.get(algorithm)
        if digest is not None:
            return ExpectedDigest(algorithm, digest)
    shasum = dist.get("shasum")
    if isinstance(shasum, str) and _SHA1_HEX.fullmatch(shasum):
        return ExpectedDigest("sha1", shasum.lower())
    raise IntegrityUnavailableError("dist has no usable sha512 or sha1 digest")


def _parse_sri(integrity: str) -> dict[DigestAlgorithm, str]:
    """Extrae de una cadena SSRI los digests de los algoritmos conocidos, en hex.

    Las entradas de algoritmos desconocidos se ignoran; una entrada mal formada de
    un algoritmo conocido lanza :class:`IntegrityUnavailableError`.
    """
    found: dict[DigestAlgorithm, str] = {}
    for entry in integrity.split():
        prefix, separator, rest = entry.partition("-")
        algorithm = _SRI_ALGORITHMS.get(prefix)
        if not separator or algorithm is None:
            continue
        encoded = rest.split("?", 1)[0]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except binascii.Error as error:
            raise IntegrityUnavailableError(f"malformed {algorithm} integrity") from error
        if len(raw) != _DIGEST_SIZES[algorithm]:
            raise IntegrityUnavailableError(f"malformed {algorithm} integrity")
        found.setdefault(algorithm, raw.hex())
    return found


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
    modelo sin conocer el formato específico de npm. Los campos con un tipo
    inesperado se tratan como ausentes.
    """
    name = packument.get("name", "")
    versions_data = versions_of(packument)
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
    raw_maintainers = packument.get("maintainers", [])
    for entry in raw_maintainers if isinstance(raw_maintainers, list) else []:
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
    ``package`` debe haber pasado :func:`validate_package_name`.
    """
    headers: Mapping[str, str] | None = {"Accept": accept} if accept else None
    return await upstream_get(client, packument_url(package), settings, headers=headers)
