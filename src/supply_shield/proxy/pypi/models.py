"""Modelos del índice simple de PyPI que sirve el espejo."""

from enum import StrEnum

from pydantic import BaseModel, Field

from supply_shield.core.model_config import FROZEN


class IndexFormat(StrEnum):
    """Formato con el que se sirve el índice simple (PEP 503 o PEP 691)."""

    HTML = "html"
    JSON = "json"


class DistributionFile(BaseModel):
    """Archivo de distribución (wheel o sdist) listado en el índice simple.

    ``sha256`` es el hash de integridad del archivo; ``requires_python`` la
    versión de Python declarada; ``yanked``/``yanked_reason`` marcan una versión
    retirada por el autor; ``core_metadata``/``metadata_sha256`` describen la
    metadata PEP 658 publicada junto al archivo; y ``size``/``upload_time`` son el
    tamaño en bytes y la fecha de subida de la PEP 700 (solo los trae el JSON).
    """

    model_config = FROZEN

    filename: str
    url: str
    sha256: str | None = None
    requires_python: str | None = None
    yanked: bool = False
    yanked_reason: str | None = None
    core_metadata: bool = False
    metadata_sha256: str | None = None
    size: int | None = Field(default=None, ge=0)
    upload_time: str | None = None


class SimpleProject(BaseModel):
    """Proyecto del índice simple con la lista de sus archivos, tal cual el upstream.

    ``versions`` es la lista de versiones de la PEP 700; ``None`` si el upstream no
    la publicó (índice HTML).
    """

    model_config = FROZEN

    name: str
    files: tuple[DistributionFile, ...] = Field(default_factory=tuple)
    versions: tuple[str, ...] | None = None
