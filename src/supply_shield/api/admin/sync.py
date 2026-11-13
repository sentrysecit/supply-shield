"""Sincroniza las excepciones de la base con la política activa del proceso.

Cada proceso aplica al instante las excepciones que crea o revoca él mismo. Las que
crea o revoca otra réplica las ve en el siguiente refresco periódico. Si la base no
responde, la política se queda con las excepciones que ya tenía: nunca se agregan
excepciones que no se pudieron leer.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import structlog
from pydantic import ValidationError

from supply_shield.db.repository import AdminRepository
from supply_shield.policy.holder import PolicyHolder
from supply_shield.policy.models import PolicyException

_logger = structlog.get_logger(__name__)

EXPIRED_GRACE = timedelta(days=30)
"""Las excepciones vencidas siguen cargadas este tiempo para que el motor explique
en el 403 que el paquete volvió a bloquearse porque su excepción venció."""


async def refresh_policy_exceptions(
    holder: PolicyHolder, repository: AdminRepository, *, today: date
) -> int:
    """Reemplaza las excepciones de la API en ``holder`` por las de la base.

    Devuelve cuántas quedaron cargadas. Una fila que no forma una excepción válida
    (p. ej. editada a mano) se descarta y se registra: descartarla solo puede volver
    más estricta la política.
    """
    async with holder.lock:
        records = await repository.policy_exceptions(today - EXPIRED_GRACE)
        loaded: dict[UUID, PolicyException] = {}
        for record in records:
            try:
                loaded[record.id] = record.to_policy_exception()
            except ValidationError:
                _logger.error("admin.exception_invalid_row", exception_id=str(record.id))
        holder.replace_dynamic(loaded)
    _logger.debug("admin.exceptions_refreshed", loaded=len(loaded), revision=holder.revision)
    return len(loaded)


async def refresh_loop(
    holder: PolicyHolder, repository: AdminRepository, *, interval_seconds: float
) -> None:
    """Refresca las excepciones cada ``interval_seconds`` hasta que se cancele la tarea."""
    while True:
        await asyncio.sleep(interval_seconds)
        await safe_refresh(holder, repository)


async def safe_refresh(holder: PolicyHolder, repository: AdminRepository) -> bool:
    """Como ``refresh_policy_exceptions``, pero registra el error en vez de lanzarlo.

    Un fallo de la base no debe tumbar el proceso ni el bucle de refresco.
    """
    try:
        await refresh_policy_exceptions(holder, repository, today=datetime.now(UTC).date())
    except Exception:  # cualquier fallo de la base se registra y se reintenta
        _logger.exception("admin.exceptions_refresh_failed")
        return False
    return True
