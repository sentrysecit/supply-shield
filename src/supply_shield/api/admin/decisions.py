"""Endpoints de administración para consultar las decisiones del proxy."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from supply_shield.api.admin.dependencies import get_admin_repository
from supply_shield.api.admin.schemas import DecisionView, ErrorDetail, Page
from supply_shield.core.enums import DecisionAction, Ecosystem
from supply_shield.core.naming import normalize_package_name
from supply_shield.db.repository import AdminRepository, DecisionQuery

MAX_PAGE_SIZE = 200

router = APIRouter(prefix="/decisions", tags=["admin: decisiones"])


def unprocessable(detail: str) -> HTTPException:
    """Error 422 con un mensaje para quien llama."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def normalized_package(ecosystem: Ecosystem | None, package: str | None) -> str | None:
    """Nombre normalizado para filtrar; 422 si falta el ecosistema o el nombre es inválido.

    El nombre se normaliza como en el proxy (`Django_Foo` = `django-foo` en PyPI),
    por eso hace falta saber el ecosistema.
    """
    if package is None:
        return None
    if ecosystem is None:
        raise unprocessable("Para filtrar por paquete indica también el ecosistema.")
    try:
        return normalize_package_name(ecosystem, package)
    except ValueError as exc:
        raise unprocessable(f"Nombre de paquete inválido: {package!r}.") from exc


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class DecisionFilters(BaseModel):
    """Filtros de ``GET /decisions``. Un parámetro desconocido da 422."""

    model_config = ConfigDict(extra="forbid")

    project: str | None = Field(default=None, max_length=255, description="Proyecto.")
    build_id: str | None = Field(default=None, max_length=255, description="Build (X-Build-Id).")
    ecosystem: Ecosystem | None = Field(default=None, description="Ecosistema.")
    package: str | None = Field(
        default=None, max_length=255, description="Nombre del paquete; exige `ecosystem`."
    )
    version: str | None = Field(default=None, max_length=100, description="Versión exacta.")
    action: DecisionAction | None = Field(default=None, description="allow, warn o block.")
    degraded: bool | None = Field(default=None, description="Tomadas en modo degradado.")
    since: datetime | None = Field(
        default=None, description="Desde (inclusive). Sin zona horaria = UTC."
    )
    until: datetime | None = Field(
        default=None, description="Hasta (exclusive). Sin zona horaria = UTC."
    )
    limit: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


@router.get(
    "",
    summary="Listar y filtrar decisiones",
    responses={422: {"model": ErrorDetail, "description": "Filtros inválidos."}},
)
async def list_decisions(
    filters: Annotated[DecisionFilters, Query()],
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> Page[DecisionView]:
    """Decisiones del proxy, de la más reciente a la más antigua.

    Cada decisión trae sus motivos y **todos** sus hallazgos, también los que solo
    se registraron o quedaron cubiertos por una excepción.
    """
    package = normalized_package(filters.ecosystem, filters.package)
    since, until = _utc(filters.since), _utc(filters.until)
    if since is not None and until is not None and since >= until:
        raise unprocessable("`since` debe ser anterior a `until`.")
    limit, offset = filters.limit, filters.offset
    records = await repository.list_decisions(
        DecisionQuery(
            project=filters.project,
            build_id=filters.build_id,
            ecosystem=filters.ecosystem,
            package=package,
            version=filters.version,
            action=filters.action,
            degraded=filters.degraded,
            since=since,
            until=until,
            limit=limit + 1,
            offset=offset,
        )
    )
    return Page[DecisionView](
        items=[DecisionView.of(record) for record in records[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(records) > limit else None,
    )
