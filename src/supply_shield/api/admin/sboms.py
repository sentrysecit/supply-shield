"""Endpoints de administración para consultar los SBOMs por proyecto o build."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from supply_shield.api.admin.decisions import MAX_PAGE_SIZE
from supply_shield.api.admin.dependencies import get_admin_repository
from supply_shield.api.admin.schemas import ErrorDetail, Page, SbomView
from supply_shield.db.repository import AdminRepository, SbomQuery

CYCLONEDX_MEDIA_TYPE = "application/vnd.cyclonedx+json"

router = APIRouter(prefix="/sboms", tags=["admin: sboms"])


class SbomFilters(BaseModel):
    """Filtros de ``GET /sboms``. Un parámetro desconocido da 422."""

    model_config = ConfigDict(extra="forbid")

    project: str | None = Field(default=None, max_length=255, description="Proyecto.")
    build_id: str | None = Field(default=None, max_length=255, description="Build (X-Build-Id).")
    limit: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


@router.get("", summary="Listar SBOMs por proyecto o build")
async def list_sboms(
    request: Request,
    filters: Annotated[SbomFilters, Query()],
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> Page[SbomView]:
    """SBOMs guardados, del más reciente al más antiguo, sin el documento.

    Cada elemento trae en `href` la ruta del CycloneDX completo.
    """
    limit, offset = filters.limit, filters.offset
    summaries = await repository.list_sboms(
        SbomQuery(
            project=filters.project, build_id=filters.build_id, limit=limit + 1, offset=offset
        )
    )
    items = [
        SbomView.of(summary, request.url_for("get_sbom", sbom_id=summary.id).path)
        for summary in summaries[:limit]
    ]
    return Page[SbomView](
        items=items,
        limit=limit,
        offset=offset,
        next_offset=offset + limit if len(summaries) > limit else None,
    )


@router.get(
    "/{sbom_id}",
    name="get_sbom",
    summary="Descargar un SBOM CycloneDX",
    response_class=JSONResponse,
    responses={
        200: {
            "description": "Documento CycloneDX tal como se guardó.",
            "content": {CYCLONEDX_MEDIA_TYPE: {"schema": {"type": "object"}}},
        },
        404: {"model": ErrorDetail, "description": "No existe un SBOM con ese id."},
    },
)
async def get_sbom(
    sbom_id: UUID,
    repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> JSONResponse:
    """Documento CycloneDX completo de un SBOM guardado."""
    document = await repository.get_sbom(sbom_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SBOM no encontrado.")
    return JSONResponse(
        content=document.document,
        media_type=CYCLONEDX_MEDIA_TYPE,
        headers={"Cache-Control": "no-store"},
    )
