"""Reescritura de URLs de archivos para que apunten al proxy.

El índice que sirve Supply-Shield no puede entregar los enlaces originales de
``files.pythonhosted.org``: el cliente debe descargar a través del proxy. Cada
enlace se reescribe a ``{base}/pypi/packages/sha256/<hash>/<ruta>`` conservando el
fragmento ``#sha256=`` (lo que pip lee para ``--require-hashes``). El hash viaja en
la ruta y no en una query porque pip y uv piden la metadata PEP 658 añadiendo
``.metadata`` al final de la URL sin fragmento: con una query quedaría
``?sha256=<hash>.metadata`` y el proxy no podría resolverla.
"""

from urllib.parse import urlsplit

from supply_shield.proxy.pypi.models import DistributionFile

FILES_HOST = "files.pythonhosted.org"
PREFIX = "/pypi"
_PACKAGES = "packages/"
HASHED_SEGMENT = "sha256"


def is_proxyable_file_url(url: str) -> bool:
    """Indica si la URL apunta a un artefacto que el proxy puede reenviar."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == FILES_HOST


def proxy_file_url(base_url: str, file: DistributionFile) -> str:
    """Reescribe la URL de ``file`` para que pase por el proxy.

    ``base_url`` es el host del proxy sin prefijo (``SUPPLY_SHIELD_PROXY_BASE_URL``).
    Las URLs que no apuntan al host de archivos conocido se dejan intactas. Si el
    archivo trae SHA-256, va en la ruta para que el proxy lo verifique al reenviar,
    y también como fragmento ``#sha256=`` para pip.
    """
    if not is_proxyable_file_url(file.url):
        return file.url
    path = urlsplit(file.url).path.lstrip("/")
    root = f"{base_url.rstrip('/')}{PREFIX}"
    if not path.startswith(_PACKAGES) or not file.sha256:
        return f"{root}/{path}"
    rest = path.removeprefix(_PACKAGES)
    return f"{root}/{_PACKAGES}{HASHED_SEGMENT}/{file.sha256}/{rest}#sha256={file.sha256}"


def upstream_file_url(path: str) -> str:
    """Reconstruye la URL upstream de un artefacto a partir de su ruta."""
    return f"https://{FILES_HOST}/{path.lstrip('/')}"
