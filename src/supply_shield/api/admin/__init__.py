"""API de administración (D4-5), protegida con un token de administrador.

Todo cuelga de ``/api/v1/admin``:

- ``/decisions``: listar y filtrar las decisiones del proxy;
- ``/sboms``: SBOMs por proyecto o build;
- ``/exceptions``: crear, listar y revocar excepciones de política;
- ``/policy``: ver la política activa y recargarla sin reiniciar.
"""

from fastapi import APIRouter, Depends

from supply_shield.api.admin import decisions, exceptions, policy, sboms
from supply_shield.api.admin.dependencies import AdminConfig
from supply_shield.api.admin.schemas import ErrorDetail
from supply_shield.api.admin.security import require_admin

router = APIRouter(
    prefix="/api/v1/admin",
    dependencies=[Depends(require_admin)],
    responses={
        401: {
            "model": ErrorDetail,
            "description": "Falta el token de administrador o no es válido.",
        },
        503: {"model": ErrorDetail, "description": "La API de administración está deshabilitada."},
    },
)
router.include_router(decisions.router)
router.include_router(sboms.router)
router.include_router(exceptions.router)
router.include_router(policy.router)

__all__ = ["AdminConfig", "router"]
