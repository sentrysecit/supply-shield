"""Endpoints de administración para crear, listar y revocar excepciones de política.

Crear o revocar una excepción la guarda en la base y la aplica de inmediato a la
política activa del proceso, bajo el mismo candado que usa el refresco periódico:
la petición siguiente ya se evalúa con ella.
"""

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from supply_shield.api.admin.decisions import MAX_PAGE_SIZE, normalized_package, unprocessable
from supply_shield.api.admin.dependencies import (
    AdminConfig,
    get_admin_config,
    get_admin_repository,
    get_known_projects,
    get_now,
    get_policy_holder,
)
from supply_shield.api.admin.schemas import (
    ErrorDetail,
    ExceptionCreate,
    ExceptionRevoke,
    ExceptionView,
    Page,
)
from supply_shield.core.enums import Ecosystem
from supply_shield.db.repository import (
    AdminRepository,
    ExceptionAlreadyRevokedError,
    ExceptionDraft,
    ExceptionNotFoundError,
    ExceptionQuery,
    ExceptionRevocation,
    ExceptionStatus,
)
from supply_shield.policy.holder import PolicyHolder

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/exceptions", tags=["admin: excepciones"])

_NOT_FOUND = "Excepción no encontrada."
_NOT_FOUND_DESCRIPTION = "No existe una excepción con ese id."


class ExceptionFilters(BaseModel):
    """Filtros de ``GET /exceptions``. Un parámetro desconocido da 422."""

    model_config = ConfigDict(extra="forbid")

    status: ExceptionStatus | None = Field(
        default=None, description="active, expired o revoked. Vacío = todas."
    )
    project: str | None = Field(default=None, max_length=255, description="Proyecto.")
    ecosystem: Ecosystem | None = Field(default=None, description="Ecosistema.")
    package: str | None = Field(
        default=None, max_length=255, description="Nombre del paquete; exige `ecosystem`."
    )
    advisory: str | None = Field(default=None, max_length=255, description="Advisory.")
    limit: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


def _check_expiry(expires: date, today: date, max_days: int) -> None:
    if expires < today:
        raise unprocessable("`expires` no puede ser una fecha pasada.")
    if expires > today + timedelta(days=max_days):
        raise unprocessable(
            f"`expires` no puede estar a más de {max_days} días: "
            "renueva la excepción con una nueva justificación cuando venza."
        )


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Crear una excepción",
    responses={422: {"model": ErrorDetail, "description": "Excepción inválida."}},
)
async def create_exception(  # noqa: PLR0913 - dependencias inyectadas por FastAPI
    *,
    body: ExceptionCreate,
    request: Request,
    response: Response,
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    holder: Annotated[PolicyHolder, Depends(get_policy_holder)],
    config: Annotated[AdminConfig, Depends(get_admin_config)],
    projects: Annotated[frozenset[str], Depends(get_known_projects)],
    now: Annotated[datetime, Depends(get_now)],
) -> ExceptionView:
    """Crea una excepción y la aplica a la política activa en el acto.

    El proyecto debe existir en `config/projects.yaml` y el vencimiento no puede
    estar a más de `SUPPLY_SHIELD_ADMIN_EXCEPTION_MAX_DAYS` días.
    """
    today = now.date()
    if body.project is not None and body.project not in projects:
        raise unprocessable(f"El proyecto {body.project!r} no existe.")
    _check_expiry(body.expires, today, config.exception_max_days)
    draft = ExceptionDraft(
        project=body.project,
        package=body.package,
        advisory=body.advisory,
        rule=body.rule,
        justification=body.justification,
        author=body.author,
        expires=body.expires,
    )
    policy_exception = draft.to_policy_exception()
    async with holder.lock:
        record = await repository.create_exception(draft)
        holder.put_exception(record.id, policy_exception)
    _logger.info(
        "admin.exception_created",
        exception_id=str(record.id),
        project=record.project,
        package=None if record.package is None else record.package.purl,
        advisory=record.advisory,
        rule=None if record.rule is None else record.rule.value,
        author=record.author,
        expires=record.expires.isoformat(),
        policy_revision=holder.revision,
        client=_client(request),
    )
    response.headers["Location"] = request.url_for("get_exception", exception_id=record.id).path
    return ExceptionView.of(record, today)


@router.get("", summary="Listar excepciones")
async def list_exceptions(
    filters: Annotated[ExceptionFilters, Query()],
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> Page[ExceptionView]:
    """Excepciones creadas por la API, de la más reciente a la más antigua.

    Las del archivo de política no están aquí: se ven en `GET /api/v1/admin/policy`.
    """
    today = now.date()
    limit, offset = filters.limit, filters.offset
    records = await repository.list_exceptions(
        ExceptionQuery(
            today=today,
            project=filters.project,
            status=filters.status,
            ecosystem=filters.ecosystem,
            package=normalized_package(filters.ecosystem, filters.package),
            advisory=filters.advisory,
            limit=limit + 1,
            offset=offset,
        )
    )
    return Page[ExceptionView](
        items=[ExceptionView.of(record, today) for record in records[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(records) > limit else None,
    )


@router.get(
    "/{exception_id}",
    name="get_exception",
    summary="Ver una excepción",
    responses={404: {"model": ErrorDetail, "description": _NOT_FOUND_DESCRIPTION}},
)
async def get_exception(
    exception_id: UUID,
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> ExceptionView:
    """Una excepción creada por la API, con su estado de hoy."""
    record = await repository.get_exception(exception_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return ExceptionView.of(record, now.date())


@router.post(
    "/{exception_id}/revoke",
    summary="Revocar una excepción",
    responses={
        404: {"model": ErrorDetail, "description": _NOT_FOUND_DESCRIPTION},
        409: {"model": ErrorDetail, "description": "La excepción ya estaba revocada."},
    },
)
async def revoke_exception(  # noqa: PLR0913 - dependencias inyectadas por FastAPI
    *,
    exception_id: UUID,
    body: ExceptionRevoke,
    request: Request,
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    holder: Annotated[PolicyHolder, Depends(get_policy_holder)],
    now: Annotated[datetime, Depends(get_now)],
) -> ExceptionView:
    """Revoca una excepción y la quita de la política activa en el acto.

    La fila no se borra: queda quién la revocó, cuándo y por qué.
    """
    revocation = ExceptionRevocation(revoked_by=body.revoked_by, reason=body.reason)
    async with holder.lock:
        try:
            record = await repository.revoke_exception(exception_id, revocation)
        except ExceptionNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from exc
        except ExceptionAlreadyRevokedError as exc:
            # Por si otra réplica la revocó y este proceso aún no refrescó.
            holder.remove_exception(exception_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="La excepción ya estaba revocada."
            ) from exc
        holder.remove_exception(record.id)
    _logger.info(
        "admin.exception_revoked",
        exception_id=str(record.id),
        revoked_by=record.revoked_by,
        policy_revision=holder.revision,
        client=_client(request),
    )
    return ExceptionView.of(record, now.date())
