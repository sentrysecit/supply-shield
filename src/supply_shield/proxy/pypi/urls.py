"""Reescritura de URLs de archivos para que apunten al proxy.

El índice que sirve Supply-Shield no puede entregar los enlaces originales de
``files.pythonhosted.org``: el cliente debe descargar a través del proxy. Cada
enlace se reescribe conservando el fragmento ``#sha256=`` (lo que pip lee para
``--require-hashes``) y añadiendo el mismo hash como query ``?sha256=`` para que
el proxy lo verifique al reenviar los bytes.
"""

from urllib.parse import urljoin, urlsplit

from supply_shield.proxy.pypi.models import DistributionFile

FILES_HOST = "files.pythonhosted.org"


def is_proxyable_file_url(url: str) -> bool:
    """Indica si la URL apunta a un artefacto que el proxy puede reenviar."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == FILES_HOST


def proxy_file_url(base_url: str, file: DistributionFile) -> str:
    """Reescribe la URL de ``file`` para que pase por el proxy.

    Las URLs que no apuntan al host de archivos conocido se dejan intactas. Al
    reescribir se conserva el ``#sha256=`` y se agrega ``?sha256=`` para la
    verificación del proxy.
    """
    if not is_proxyable_file_url(file.url):
        return file.url
    path = urlsplit(file.url).path
    target = urljoin(base_url, path.lstrip("/"))
    if file.sha256:
        target = f"{target}?sha256={file.sha256}#sha256={file.sha256}"
    return target


def upstream_file_url(path: str) -> str:
    """Reconstruye la URL upstream de un artefacto a partir de su ruta."""
    return f"https://{FILES_HOST}/{path.lstrip('/')}"
