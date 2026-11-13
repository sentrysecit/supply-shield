"""Endpoints de administración para ver la política activa y recargarla en caliente."""

import asyncio
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from supply_shield.api.admin.dependencies import AdminConfig, get_admin_config, get_policy_holder
from supply_shield.api.admin.schemas import ErrorDetail, PolicyView
from supply_shield.policy.holder import PolicyHolder, read_policy_file
from supply_shield.policy.loader import PolicyLoadError

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/policy", tags=["admin: política"])


@router.get("", summary="Ver la política activa")
async def get_policy(holder: Annotated[PolicyHolder, Depends(get_policy_holder)]) -> PolicyView:
    """Política que aplica el proxy ahora: su origen, su sha256 y su contenido."""
    return PolicyView.of(holder)


@router.post(
    "/reload",
    summary="Recargar la política sin reiniciar",
    responses={
        422: {
            "model": ErrorDetail,
            "description": "El archivo no existe o no es válido; sigue la política anterior.",
        }
    },
)
async def reload_policy(
    request: Request,
    holder: Annotated[PolicyHolder, Depends(get_policy_holder)],
    config: Annotated[AdminConfig, Depends(get_admin_config)],
) -> PolicyView:
    """Vuelve a leer y validar `SUPPLY_SHIELD_POLICY_FILE` y la activa.

    Si el archivo no existe o no es válido, **la política anterior sigue activa**:
    un error al editar el YAML no deja al proxy sin reglas. Las excepciones creadas
    por la API se conservan. El archivo es siempre el configurado: la petición no
    puede elegir otra ruta.
    """
    client = request.client.host if request.client else None
    async with holder.lock:
        try:
            policy, source = await asyncio.to_thread(read_policy_file, config.policy_file)
        except FileNotFoundError as exc:
            _logger.error("admin.policy_reload_failed", reason="missing", client=client)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"No existe el archivo de política {config.policy_file}; "
                "sigue activa la política anterior.",
            ) from exc
        except PolicyLoadError as exc:
            _logger.error("admin.policy_reload_failed", reason="invalid", client=client)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"La política no es válida; sigue activa la anterior. {exc}",
            ) from exc
        holder.replace_base(policy, source)
    _logger.info(
        "admin.policy_reloaded",
        sha256=source.sha256,
        policy_revision=holder.revision,
        client=client,
    )
    return PolicyView.of(holder)
