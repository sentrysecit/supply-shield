"""Conversión de la JSON API de PyPI al modelo común ``UpstreamMetadata``.

El índice simple no publica fechas de publicación ni mantenedores. Eso vive en
``https://pypi.org/pypi/<nombre>/json``, de donde se toman las fechas por versión
(la primera subida de sus archivos) y los responsables. Es el ``UpstreamMetadata``
que consumen los detectores de riesgo y de vulnerabilidades.
"""

from datetime import UTC, datetime

from supply_shield.core import Ecosystem, Maintainer, PackageRef, PackageRelease, UpstreamMetadata
from supply_shield.core.naming import clean_version


def _as_dict(value: object) -> dict[str, object]:
    """Devuelve ``value`` como diccionario de claves de texto, o uno vacío."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _parse_time(raw: object) -> datetime | None:
    """Parsea una fecha de PyPI (ISO 8601, a veces con ``Z``) a ``datetime`` UTC."""
    if not isinstance(raw, str) or not raw:
        return None
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _earliest_upload(files: object) -> datetime | None:
    """Primera fecha de subida entre los archivos de una versión."""
    if not isinstance(files, list):
        return None
    stamps = [
        stamp
        for stamp in (_parse_time(_as_dict(entry).get("upload_time_iso_8601")) for entry in files)
        if stamp is not None
    ]
    return min(stamps) if stamps else None


def _maintainers(info: dict[str, object]) -> tuple[Maintainer, ...]:
    """Responsables declarados en ``info``: mantenedor y, si falta, autor."""
    name = info.get("maintainer") or info.get("author")
    email = info.get("maintainer_email") or info.get("author_email")
    if not isinstance(name, str) or not name.strip():
        return ()
    return (
        Maintainer(
            name=name.strip(),
            email=email.strip() if isinstance(email, str) and email.strip() else None,
        ),
    )


def _latest_version(info: dict[str, object]) -> str | None:
    """``info.version`` si es una versión válida; si no (p. ej. ``"0.1 beta"``), ``None``."""
    latest = info.get("version")
    if not isinstance(latest, str):
        return None
    try:
        return clean_version(latest)
    except ValueError:
        return None


def parse_pypi_metadata(name: str, payload: object) -> UpstreamMetadata:
    """Convierte la respuesta de la JSON API de PyPI en ``UpstreamMetadata``."""
    data = _as_dict(payload)
    info = _as_dict(data.get("info"))
    versions: list[PackageRelease] = []
    releases = data.get("releases")
    if isinstance(releases, dict):
        for version, files in releases.items():
            if not isinstance(version, str):
                continue
            try:
                release = PackageRelease(version=version, published_at=_earliest_upload(files))
            except ValueError:
                continue
            versions.append(release)
    package = PackageRef.of(Ecosystem.PYPI, name, _latest_version(info))
    return UpstreamMetadata(
        package=package,
        versions=tuple(versions),
        scripts={},
        maintainers=_maintainers(info),
    )
