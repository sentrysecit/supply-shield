"""Nombres canónicos y URLs de paquete para npm y PyPI."""

import re
from urllib.parse import quote

from supply_shield.core.enums import Ecosystem

_PYPI_SEPARATORS = re.compile(r"[-_.]+")
_PYPI_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NPM_NAME = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-!]*$")


def normalize_package_name(ecosystem: Ecosystem, name: str) -> str:
    """Devuelve el nombre canónico del paquete en ``ecosystem``.

    PyPI sigue la PEP 503: las mayúsculas y los separadores ``-``, ``_`` y ``.``
    no distinguen paquetes. npm pasa a minúsculas, pero ``_`` y ``-`` siguen
    siendo distintos, y un ``@scope`` forma parte del nombre.
    """
    stripped = name.strip()
    if ecosystem is Ecosystem.PYPI:
        normalized = _PYPI_SEPARATORS.sub("-", stripped).lower()
        pattern = _PYPI_NAME
    else:
        normalized = stripped.lower()
        pattern = _NPM_NAME
    if pattern.fullmatch(normalized) is None:
        msg = f"invalid {ecosystem.value} package name: {name!r}"
        raise ValueError(msg)
    return normalized


def clean_version(version: str) -> str:
    """Quita el espacio alrededor y rechaza versiones que no caben en un purl."""
    cleaned = version.strip()
    if _VERSION.fullmatch(cleaned) is None:
        msg = f"invalid package version: {version!r}"
        raise ValueError(msg)
    return cleaned


def build_purl(ecosystem: Ecosystem, name: str, version: str | None) -> str:
    """Construye la URL de paquete a partir de un nombre ya normalizado."""
    if ecosystem is Ecosystem.PYPI:
        base = f"pkg:pypi/{quote(name, safe='')}"
    elif name.startswith("@"):
        scope, _, package_name = name.partition("/")
        base = f"pkg:npm/{quote(scope, safe='')}/{quote(package_name, safe='')}"
    else:
        base = f"pkg:npm/{quote(name, safe='')}"
    if version is None:
        return base
    return f"{base}@{quote(version, safe='')}"
