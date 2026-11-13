"""Endpoints de administración para crear, listar y revocar tokens de proyecto.

El token se genera en el servidor y se devuelve **una sola vez**, al crearlo; la
base guarda solo su hash y su ``key_id`` público. Revocar no borra el registro.

Hasta que la autenticación del proxy lea estos tokens (ADR 0010), el proxy sigue
aceptando solo los de ``config/projects.yaml``.
"""

import asyncio
from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from supply_shield.api.admin.decisions import MAX_PAGE_SIZE, unprocessable
from supply_shield.api.admin.dependencies import (
    get_known_projects,
    get_now,
    get_project_token_repository,
)
from supply_shield.api.admin.schemas import (
    ErrorDetail,
    Page,
    ProjectTokenCreate,
    ProjectTokenCreated,
    ProjectTokenRevoke,
    ProjectTokenView,
)
from supply_shield.api.auth import issue_project_token
from supply_shield.db.project_tokens import (
    ProjectTokenAlreadyRevokedError,
    ProjectTokenDraft,
    ProjectTokenNotFoundError,
    ProjectTokenQuery,
    ProjectTokenRepository,
    ProjectTokenRevocation,
    ProjectTokenStatus,
)

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project}/tokens", tags=["admin: tokens de proyecto"])

ProjectName = Annotated[
    str, Path(min_length=1, max_length=255, description="Proyecto de `config/projects.yaml`.")
]

_NOT_FOUND = "Token no encontrado."
_NOT_FOUND_DESCRIPTION = "No existe un token con ese id en ese proyecto."


class ProjectTokenFilters(BaseModel):
    """Filtros de ``GET /projects/{project}/tokens``. Un parámetro desconocido da 422."""

    model_config = ConfigDict(extra="forbid")

    status: ProjectTokenStatus | None = Field(
        default=None, description="active, expired o revoked. Vacío = todos."
    )
    limit: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


def _client(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Crear un token de proyecto",
    responses={
        404: {"model": ErrorDetail, "description": "El proyecto no existe."},
        422: {"model": ErrorDetail, "description": "Token inválido."},
    },
)
async def create_project_token(  # noqa: PLR0913 - dependencias inyectadas por FastAPI
    *,
    project: ProjectName,
    body: ProjectTokenCreate,
    request: Request,
    response: Response,
    repository: Annotated[ProjectTokenRepository, Depends(get_project_token_repository)],
    projects: Annotated[frozenset[str], Depends(get_known_projects)],
    now: Annotated[datetime, Depends(get_now)],
) -> ProjectTokenCreated:
    """Genera un token para el proyecto y lo devuelve **una sola vez**.

    El proyecto debe existir en `config/projects.yaml`. El servicio guarda solo el
    hash: si se pierde el token, hay que crear otro y revocar el anterior.

    **Importante:** hasta que la autenticación del proxy lea la base (ADR 0010), el
    proxy acepta solo los tokens de `config/projects.yaml`.
    """
    if project not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El proyecto {project!r} no existe en config/projects.yaml.",
        )
    if body.expires is not None and body.expires < now.date():
        raise unprocessable("`expires` no puede ser una fecha pasada.")
    # pbkdf2 tarda decenas de milisegundos: fuera del bucle de eventos.
    issued = await asyncio.to_thread(issue_project_token)
    record = await repository.create_project_token(
        ProjectTokenDraft(
            project=project,
            name=body.name,
            key_id=issued.key_id,
            token_hash=issued.token_hash,
            created_by=body.created_by,
            expires=body.expires,
        )
    )
    _logger.info(
        "admin.project_token_created",
        project=project,
        token_id=str(record.id),
        key_id=record.key_id,
        name=record.name,
        created_by=record.created_by,
        expires=None if record.expires is None else record.expires.isoformat(),
        client=_client(request),
    )
    response.headers["Location"] = request.url_for(
        "get_project_token", project=project, token_id=record.id
    ).path
    view = ProjectTokenView.of(record, now)
    return ProjectTokenCreated(**view.model_dump(), token=issued.token.get_secret_value())


@router.get("", summary="Listar los tokens de un proyecto")
async def list_project_tokens(
    project: ProjectName,
    filters: Annotated[ProjectTokenFilters, Query()],
    repository: Annotated[ProjectTokenRepository, Depends(get_project_token_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> Page[ProjectTokenView]:
    """Tokens creados por la API para el proyecto, del más reciente al más antiguo.

    No incluye el token de `config/projects.yaml` ni, nunca, el token o su hash.
    También lista los de un proyecto que ya no está en el YAML, para poder revocarlos.
    """
    limit, offset = filters.limit, filters.offset
    records = await repository.list_project_tokens(
        ProjectTokenQuery(
            project=project, now=now, status=filters.status, limit=limit + 1, offset=offset
        )
    )
    return Page[ProjectTokenView](
        items=[ProjectTokenView.of(record, now) for record in records[:limit]],
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(records) > limit else None,
    )


@router.get(
    "/{token_id}",
    name="get_project_token",
    summary="Ver un token de proyecto",
    responses={404: {"model": ErrorDetail, "description": _NOT_FOUND_DESCRIPTION}},
)
async def get_project_token(
    project: ProjectName,
    token_id: UUID,
    repository: Annotated[ProjectTokenRepository, Depends(get_project_token_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> ProjectTokenView:
    """Un token del proyecto, con su estado actual (sin el token ni su hash)."""
    record = await repository.get_project_token(project, token_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return ProjectTokenView.of(record, now)


@router.post(
    "/{token_id}/revoke",
    summary="Revocar un token de proyecto",
    responses={
        404: {"model": ErrorDetail, "description": _NOT_FOUND_DESCRIPTION},
        409: {"model": ErrorDetail, "description": "El token ya estaba revocado."},
    },
)
async def revoke_project_token(  # noqa: PLR0913 - dependencias inyectadas por FastAPI
    *,
    project: ProjectName,
    token_id: UUID,
    body: ProjectTokenRevoke,
    request: Request,
    repository: Annotated[ProjectTokenRepository, Depends(get_project_token_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> ProjectTokenView:
    """Revoca un token. El registro no se borra: queda quién lo revocó, cuándo y por qué."""
    revocation = ProjectTokenRevocation(revoked_by=body.revoked_by, reason=body.reason)
    try:
        record = await repository.revoke_project_token(project, token_id, revocation)
    except ProjectTokenNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from exc
    except ProjectTokenAlreadyRevokedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="El token ya estaba revocado."
        ) from exc
    _logger.info(
        "admin.project_token_revoked",
        project=project,
        token_id=str(record.id),
        key_id=record.key_id,
        revoked_by=record.revoked_by,
        client=_client(request),
    )
    return ProjectTokenView.of(record, now)
