"""Render del índice simple (PEP 503 HTML y PEP 691 JSON) con URLs del proxy.

A partir del ``SimpleProject`` parseado del upstream, se genera la respuesta que
recibe el cliente: mismos archivos, mismos hashes y mismos atributos PEP 658 y
*yanked*, pero con los enlaces reescritos hacia el proxy. Un archivo cuyo enlace el
proxy no puede servir se quita del índice: entregarlo tal cual dejaría que el
cliente lo descargara sin pasar por el firewall.
"""

from html import escape
from urllib.parse import urlsplit

import structlog

from supply_shield.proxy.pypi.models import DistributionFile, SimpleProject
from supply_shield.proxy.pypi.urls import proxy_file_url

_log = structlog.get_logger(__name__)

# PEP 700 (1.1) exige ``versions`` y el ``size`` de cada archivo; sin ellos se declara 1.0.
_API_VERSION_FULL = "1.1"
_API_VERSION_BASE = "1.0"


def _served_files(project: SimpleProject, base_url: str) -> list[tuple[DistributionFile, str]]:
    """Archivos del índice con su URL en el proxy; descarta los que no se pueden servir."""
    served: list[tuple[DistributionFile, str]] = []
    for file in project.files:
        url = proxy_file_url(base_url, file)
        if url is None:
            _log.warning(
                "pypi.index.file_dropped",
                package=project.name,
                filename=file.filename,
                host=urlsplit(file.url).hostname or "",
                reason="url not served by the proxy",
            )
            continue
        served.append((file, url))
    return served


def _html_anchor(file: DistributionFile, url: str) -> str:
    """Devuelve la etiqueta ``<a>`` de un archivo con sus atributos PEP 658."""
    attributes = [f'href="{escape(url, quote=True)}"']
    if file.requires_python is not None:
        attributes.append(f'data-requires-python="{escape(file.requires_python, quote=True)}"')
    if file.core_metadata:
        metadata = f"sha256={file.metadata_sha256}" if file.metadata_sha256 else ""
        attributes.append(f'data-core-metadata="{escape(metadata, quote=True)}"')
    if file.yanked:
        attributes.append(f'data-yanked="{escape(file.yanked_reason or "", quote=True)}"')
    return f"<a {' '.join(attributes)}>{escape(file.filename)}</a>"


def render_html(project: SimpleProject, base_url: str) -> str:
    """Renderiza el índice simple como HTML (PEP 503)."""
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta name="pypi:repository-version" content="1.1">',
        f"<title>Links for {escape(project.name)}</title>",
        "</head>",
        "<body>",
        f"<h1>Links for {escape(project.name)}</h1>",
    ]
    lines.extend(_html_anchor(file, url) for file, url in _served_files(project, base_url))
    lines += ["</body>", "</html>"]
    return "\n".join(lines)


def _json_file(file: DistributionFile, url: str) -> dict[str, object]:
    """Convierte un archivo en la entrada JSON de PEP 691 (con los campos de la PEP 700)."""
    entry: dict[str, object] = {
        "filename": file.filename,
        "url": url,
        # PEP 691: ``hashes`` va siempre, aunque esté vacío.
        "hashes": {"sha256": file.sha256} if file.sha256 else {},
    }
    if file.requires_python is not None:
        entry["requires-python"] = file.requires_python
    if file.core_metadata:
        entry["core-metadata"] = {"sha256": file.metadata_sha256} if file.metadata_sha256 else True
    entry["yanked"] = file.yanked_reason if (file.yanked and file.yanked_reason) else file.yanked
    if file.size is not None:
        entry["size"] = file.size
    if file.upload_time is not None:
        entry["upload-time"] = file.upload_time
    return entry


def render_json(project: SimpleProject, base_url: str) -> dict[str, object]:
    """Renderiza el índice simple como JSON (PEP 691).

    Declara la versión 1.1 de la API solo si puede cumplir la PEP 700: el upstream
    publicó ``versions`` y el ``size`` de cada archivo servido. Si no, declara 1.0.
    """
    served = _served_files(project, base_url)
    complete = project.versions is not None and all(file.size is not None for file, _ in served)
    payload: dict[str, object] = {
        "meta": {"api-version": _API_VERSION_FULL if complete else _API_VERSION_BASE},
        "name": project.name,
        "files": [_json_file(file, url) for file, url in served],
    }
    if project.versions is not None:
        payload["versions"] = list(project.versions)
    return payload
