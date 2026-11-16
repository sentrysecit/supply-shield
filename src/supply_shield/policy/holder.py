"""Política activa del proceso: la del archivo más las excepciones creadas por la API.

El motor (``evaluate``) es una función pura que recibe una ``Policy``. Este módulo
guarda cuál es esa política en cada momento y la cambia sin reiniciar:

- ``replace_base`` la cambia por una recién leída del YAML (recarga en caliente);
- ``put_exception`` / ``remove_exception`` agregan o quitan una excepción de la API;
- ``replace_dynamic`` reemplaza todas las de la API por las que hay en la base.

Cada cambio construye una ``Policy`` nueva e inmutable y la publica de una vez, así
que una evaluación en curso nunca ve una política a medio armar. ``revision`` sube
en cada cambio: quien cachee decisiones (D3-6) debe incluirla en la clave para que
una excepción nueva se note en la petición siguiente.
"""

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from supply_shield.core.model_config import FROZEN
from supply_shield.policy.loader import PolicyLoadError, parse_policy
from supply_shield.policy.models import Policy, PolicyException

PolicyOrigin = Literal["file", "default"]


class PolicySource(BaseModel):
    """De dónde salió la política base y cuándo se cargó."""

    model_config = FROZEN

    origin: PolicyOrigin
    path: str
    sha256: str | None = None
    loaded_at: datetime


class PolicyHolder:
    """Guarda la política activa y la reemplaza de forma atómica.

    ``lock`` serializa las operaciones que leen o escriben la base y luego tocan la
    política (crear, revocar, refrescar). Sin él, un refresco que leyó la base antes
    de una revocación podría volver a publicar la excepción revocada.
    """

    def __init__(self, base: Policy, source: PolicySource) -> None:
        self._base = base
        self._source = source
        self._dynamic: dict[UUID, PolicyException] = {}
        self._revision = 0
        self._current = base
        self.lock = asyncio.Lock()

    @property
    def current(self) -> Policy:
        """Política efectiva: la del archivo con las excepciones de la API al final."""
        return self._current

    @property
    def base(self) -> Policy:
        """Política tal como se leyó del archivo (o la de fábrica)."""
        return self._base

    @property
    def source(self) -> PolicySource:
        """Origen de la política base."""
        return self._source

    @property
    def revision(self) -> int:
        """Contador que sube con cada cambio de la política efectiva."""
        return self._revision

    @property
    def dynamic_exceptions(self) -> Mapping[UUID, PolicyException]:
        """Excepciones de la API que están en la política efectiva, por id."""
        return dict(self._dynamic)

    def replace_base(self, base: Policy, source: PolicySource) -> None:
        """Cambia la política del archivo y conserva las excepciones de la API."""
        self._base = base
        self._source = source
        self._publish()

    def put_exception(self, exception_id: UUID, exception: PolicyException) -> None:
        """Agrega (o reemplaza) una excepción creada por la API."""
        self._dynamic[exception_id] = exception
        self._publish()

    def remove_exception(self, exception_id: UUID) -> bool:
        """Quita una excepción de la API. Devuelve ``False`` si no estaba."""
        if self._dynamic.pop(exception_id, None) is None:
            return False
        self._publish()
        return True

    def replace_dynamic(self, exceptions: Mapping[UUID, PolicyException]) -> bool:
        """Reemplaza todas las excepciones de la API por ``exceptions``.

        Si son las mismas no publica nada ni sube ``revision``: el refresco periódico
        no debe invalidar las decisiones cacheadas cuando nada cambió. Devuelve si
        hubo cambio.
        """
        fresh = dict(exceptions)
        if fresh == self._dynamic:
            return False
        self._dynamic = fresh
        self._publish()
        return True

    def _publish(self) -> None:
        exceptions = self._base.exceptions + tuple(self._dynamic.values())
        self._current = self._base.model_copy(update={"exceptions": exceptions})
        self._revision += 1


def read_policy_file(path: Path, *, now: datetime | None = None) -> tuple[Policy, PolicySource]:
    """Lee y valida la política de ``path`` y devuelve también su origen.

    Lanza ``FileNotFoundError`` si no existe y ``PolicyLoadError`` si es inválida.
    El sha256 del archivo permite comprobar qué versión quedó cargada.
    """
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"policy {path} is not valid UTF-8: {exc}"
        raise PolicyLoadError(msg) from exc
    policy = parse_policy(text, source=str(path))
    source = PolicySource(
        origin="file",
        path=str(path),
        sha256=hashlib.sha256(raw).hexdigest(),
        loaded_at=now or datetime.now(UTC),
    )
    return policy, source


def default_source(path: Path, *, now: datetime | None = None) -> PolicySource:
    """Origen de la política de fábrica, usada cuando ``path`` no existe."""
    return PolicySource(origin="default", path=str(path), loaded_at=now or datetime.now(UTC))
