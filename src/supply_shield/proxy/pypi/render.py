"""Render del índice simple (PEP 503 HTML y PEP 691 JSON) con URLs del proxy.

A partir del ``SimpleProject`` parseado del upstream, se genera la respuesta que
recibe el cliente: mismos archivos, mismos hashes y mismos atributos PEP 658 y
*yanked*, pero con los enlaces reescritos hacia el proxy.
"""

from html import escape

from supply_shield.proxy.pypi.models import DistributionFile, SimpleProject
from supply_shield.proxy.pypi.urls import proxy_file_url


def _html_anchor(base_url: str, file: DistributionFile) -> str:
    """Devuelve la etiqueta ``<a>`` de un archivo con sus atributos PEP 658."""
    attributes = [f'href="{escape(proxy_file_url(base_url, file), quote=True)}"']
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
    lines.extend(_html_anchor(base_url, file) for file in project.files)
    lines += ["</body>", "</html>"]
    return "\n".join(lines)


def _json_file(base_url: str, file: DistributionFile) -> dict[str, object]:
    """Convierte un archivo en la entrada JSON de PEP 691."""
    entry: dict[str, object] = {
        "filename": file.filename,
        "url": proxy_file_url(base_url, file),
    }
    if file.sha256:
        entry["hashes"] = {"sha256": file.sha256}
    if file.requires_python is not None:
        entry["requires-python"] = file.requires_python
    if file.core_metadata:
        entry["core-metadata"] = {"sha256": file.metadata_sha256} if file.metadata_sha256 else True
    entry["yanked"] = file.yanked_reason if (file.yanked and file.yanked_reason) else file.yanked
    return entry


def render_json(project: SimpleProject, base_url: str) -> dict[str, object]:
    """Renderiza el índice simple como JSON (PEP 691)."""
    return {
        "meta": {"api-version": "1.1"},
        "name": project.name,
        "files": [_json_file(base_url, file) for file in project.files],
    }
