"""Autenticación de la API de administración con un token propio.

El token de administrador es distinto de los de proyecto: un token de proyecto
(el que va en ``.npmrc`` o ``pip.conf`` de cada pipeline) nunca abre esta API.
Solo se acepta por ``Authorization: Bearer`` y se compara contra un hash pbkdf2
(``SUPPLY_SHIELD_ADMIN_TOKEN_HASH``), igual que los de proyecto (ADR 0006). El
hash se calcula en un hilo aparte para no frenar el bucle de eventos.
"""

import asyncio
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from supply_shield.api.admin.dependencies import AdminConfig, get_admin_config
from supply_shield.api.auth import verify_token

_logger = structlog.get_logger(__name__)

_NO_STORE = {"Cache-Control": "no-store"}
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="supply-shield-admin"', **_NO_STORE}

admin_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AdminToken",
    description="Token de administrador (`Authorization: Bearer <token>`).",
)


async def require_admin(
    request: Request,
    response: Response,
    config: Annotated[AdminConfig, Depends(get_admin_config)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(admin_bearer)],
) -> None:
    """Deja pasar solo peticiones con el token de administrador.

    - 503 si la API no tiene token configurado (deshabilitada).
    - 401 si falta el token, no es Bearer o no coincide.
    """
    response.headers.update(_NO_STORE)
    client = request.client.host if request.client else None
    if config.token_hash is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La API de administración está deshabilitada: "
            "configura SUPPLY_SHIELD_ADMIN_TOKEN_HASH.",
            headers=_NO_STORE,
        )
    if credentials is None or not credentials.credentials.strip():
        _logger.info("admin.auth_missing_token", client=client, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el token de administrador.",
            headers=_CHALLENGE,
        )
    valid = await asyncio.to_thread(
        verify_token, credentials.credentials.strip(), config.token_hash.get_secret_value()
    )
    if not valid:
        _logger.warning("admin.auth_invalid_token", client=client, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de administrador inválido.",
            headers=_CHALLENGE,
        )
