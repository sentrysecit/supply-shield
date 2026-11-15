"""Parseo del índice simple upstream, tanto HTML (PEP 503) como JSON (PEP 691).

Un único modelo ``DistributionFile`` abstrae las diferencias entre formatos: los
atributos ``data-requires-python``, ``data-core-metadata`` (PEP 658) y
``data-yanked`` del HTML son las claves ``requires-python``, ``core-metadata`` y
``yanked`` del JSON. El hash SHA-256 se toma del fragmento ``#sha256=`` del
``href`` en HTML y de ``hashes.sha256`` en JSON.
"""

from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlsplit

from supply_shield.proxy.pypi.models import DistributionFile


def _as_dict(value: object) -> dict[str, object]:
    """Devuelve ``value`` como diccionario de claves de texto, o uno vacío."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _sha256_from_metadata(value: object) -> str | None:
    """Extrae el hash de ``sha256=<hex>`` de un atributo de metadata PEP 658."""
    if not isinstance(value, str):
        return None
    key, separator, digest = value.partition("=")
    if separator and key.strip().lower() == "sha256":
        return digest.strip() or None
    return None


def _core_metadata(value: object) -> tuple[bool, str | None]:
    """Interpreta ``core-metadata``: booleano, cadena ``sha256=`` o diccionario."""
    if value is True:
        return True, None
    if isinstance(value, dict):
        digest = value.get("sha256")
        return True, digest if isinstance(digest, str) else None
    if isinstance(value, str) and value:
        return True, _sha256_from_metadata(value)
    return False, None


def _yanked(value: object) -> tuple[bool, str | None]:
    """Interpreta ``yanked``: booleano o la razón del retiro."""
    if value is True:
        return True, None
    if isinstance(value, str):
        return True, value or None
    return False, None


class _AnchorCollector(HTMLParser):
    """Recolecta los atributos de cada ``<a>`` del índice HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Guarda los atributos de las etiquetas de enlace."""
        if tag == "a":
            self.anchors.append(dict(attrs))


def _file_from_anchor(attrs: dict[str, str | None]) -> DistributionFile | None:
    """Construye un ``DistributionFile`` a partir de los atributos de un enlace."""
    href = attrs.get("href")
    if not href:
        return None
    parts = urlsplit(href)
    filename = unquote(parts.path.rsplit("/", 1)[-1])
    sha256 = None
    for key, value in parse_qsl(parts.fragment):
        if key == "sha256":
            sha256 = value or None
    metadata = attrs.get("data-core-metadata") or attrs.get("data-dist-info-metadata")
    core_metadata, metadata_sha256 = _core_metadata(metadata)
    yanked, yanked_reason = _yanked(attrs.get("data-yanked"))
    return DistributionFile(
        filename=filename,
        url=href,
        sha256=sha256,
        requires_python=attrs.get("data-requires-python"),
        yanked=yanked,
        yanked_reason=yanked_reason,
        core_metadata=core_metadata,
        metadata_sha256=metadata_sha256,
    )


def parse_simple_html(text: str) -> tuple[DistributionFile, ...]:
    """Convierte el índice simple HTML (PEP 503) en la lista de archivos."""
    collector = _AnchorCollector()
    collector.feed(text)
    files = (_file_from_anchor(attrs) for attrs in collector.anchors)
    return tuple(file for file in files if file is not None)


def parse_simple_json(payload: object) -> tuple[DistributionFile, ...]:
    """Convierte el índice simple JSON (PEP 691) en la lista de archivos."""
    raw_files = _as_dict(payload).get("files")
    if not isinstance(raw_files, list):
        return ()
    files: list[DistributionFile] = []
    for entry in raw_files:
        record = _as_dict(entry)
        filename = record.get("filename")
        url = record.get("url")
        if not isinstance(filename, str) or not isinstance(url, str):
            continue
        sha256 = _as_dict(record.get("hashes")).get("sha256")
        core_metadata, metadata_sha256 = _core_metadata(record.get("core-metadata"))
        yanked, yanked_reason = _yanked(record.get("yanked"))
        requires_python = record.get("requires-python")
        files.append(
            DistributionFile(
                filename=filename,
                url=url,
                sha256=sha256 if isinstance(sha256, str) else None,
                requires_python=requires_python if isinstance(requires_python, str) else None,
                yanked=yanked,
                yanked_reason=yanked_reason,
                core_metadata=core_metadata,
                metadata_sha256=metadata_sha256,
            )
        )
    return tuple(files)
