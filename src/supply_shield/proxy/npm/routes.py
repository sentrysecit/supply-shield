"""Rutas FastAPI del espejo del registro npm (packuments y tarballs).

Todas las rutas exigen el token de proyecto (ADR 0006): npm lo envía si el
``.npmrc`` tiene ``//<host>/npm/:_authToken=...``. El nombre del paquete llega ya
decodificado una vez por el servidor ASGI y no se vuelve a decodificar.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from supply_shield.api.auth import get_request_context
from supply_shield.config import Settings, get_settings
from supply_shield.core.request_context import RequestContext
from supply_shield.proxy.dependencies import get_upstream_client
from supply_shield.proxy.npm.packument import (
    IntegrityUnavailableError,
    InvalidPackumentError,
    fetch_packument,
    parse_packument,
    rewrite_tarballs,
    validate_package_name,
)
from supply_shield.proxy.npm.tarball import TarballNotFoundError, open_tarball, resolve_tarball
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    UpstreamTooLargeError,
)

router = APIRouter(prefix="/npm", tags=["npm"])
_log = structlog.get_logger(__name__)

# Cómo responde el proxy a cada error del upstream: status y mensaje para el cliente.
_UPSTREAM_ERRORS: tuple[tuple[type[Exception], int, str], ...] = (
    (UpstreamHostError, status.HTTP_403_FORBIDDEN, "upstream host not allowed"),
    (TarballNotFoundError, status.HTTP_404_NOT_FOUND, "tarball not found"),
    (InvalidPackumentError, status.HTTP_502_BAD_GATEWAY, "invalid upstream response"),
    (IntegrityUnavailableError, status.HTTP_502_BAD_GATEWAY, "invalid upstream response"),
    (UpstreamTooLargeError, status.HTTP_502_BAD_GATEWAY, "upstream response too large"),
    (UpstreamRequestError, status.HTTP_502_BAD_GATEWAY, "upstream unavailable"),
)
_HANDLED_ERRORS: tuple[type[Exception], ...] = tuple(error for error, _, _ in _UPSTREAM_ERRORS)


@dataclass(frozen=True, slots=True)
class _ProxyRequest:
    """Lo que necesita cada ruta npm: ajustes, cliente upstream y proyecto autenticado."""

    settings: Settings
    client: httpx.AsyncClient
    context: RequestContext


def _proxy_request(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_upstream_client)],
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> _ProxyRequest:
    """Dependencia que autentica la petición y reúne lo que usan las rutas."""
    return _ProxyRequest(settings=settings, client=client, context=context)


ProxyRequestDep = Annotated[_ProxyRequest, Depends(_proxy_request)]


def _error(message: str, status_code: int) -> JSONResponse:
    """Respuesta de error JSON con la forma que usan todas las rutas npm."""
    return JSONResponse({"error": message}, status_code=status_code)


def _upstream_error(error: Exception, event: str, **fields: Any) -> JSONResponse:
    """Registra un error del upstream y lo traduce a su respuesta HTTP."""
    for error_type, status_code, message in _UPSTREAM_ERRORS:
        if isinstance(error, error_type):
            _log.warning(event, reason=type(error).__name__, status=status_code, **fields)
            return _error(message, status_code)
    raise error


async def _serve_packument(package: str, request: Request, proxy: _ProxyRequest) -> Response:
    """Lógica compartida para servir el packument de un paquete (scoped o no)."""
    project = proxy.context.project
    try:
        validate_package_name(package)
    except ValueError:
        _log.warning("npm.packument.invalid_name", package=package, project=project)
        return _error("invalid package name", status.HTTP_400_BAD_REQUEST)
    accept = request.headers.get("accept")
    _log.info("npm.packument.request", package=package, accept=accept, project=project)
    try:
        upstream_response = await fetch_packument(
            proxy.client, package, proxy.settings, accept=accept
        )
        packument = (
            parse_packument(upstream_response)
            if upstream_response.status_code == status.HTTP_200_OK
            else None
        )
        if packument is not None:
            rewrite_tarballs(packument, proxy.settings.proxy_base_url)
    except _HANDLED_ERRORS as error:
        return _upstream_error(error, "npm.packument.upstream_error", package=package)

    media_type = upstream_response.headers.get("content-type", "application/json")
    if packument is not None:
        _log.info("npm.packument.served", package=package, status=status.HTTP_200_OK)
        return JSONResponse(content=packument, media_type=media_type)

    _log.info(
        "npm.packument.upstream_status",
        package=package,
        status=upstream_response.status_code,
    )
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=media_type,
    )


@router.get("/{package}")
async def get_packument(package: str, request: Request, proxy: ProxyRequestDep) -> Response:
    """Sirve el packument de un paquete npm sin scope (p. ej. `express`)."""
    return await _serve_packument(package, request, proxy)


@router.get("/{scope}/{name}")
async def get_scoped_packument(
    scope: str,
    name: str,
    request: Request,
    proxy: ProxyRequestDep,
) -> Response:
    """Sirve el packument de un paquete npm con scope (p. ej. `@types/node`).

    Si ``scope`` no empieza por ``@`` el nombre no es válido y se responde 400: así
    rutas del registro como ``/express/latest`` no se reenvían al upstream.
    """
    return await _serve_packument(f"{scope}/{name}", request, proxy)


async def _serve_tarball(package: str, filename: str, proxy: _ProxyRequest) -> Response:
    """Lógica compartida para resolver y servir un tarball (scoped o no).

    La descarga upstream se abre y se valida antes de responder, así que un error
    del upstream llega al cliente como 403, 404 o 502 y no como un 200 cortado.
    """
    project = proxy.context.project
    try:
        validate_package_name(package)
    except ValueError:
        _log.warning("npm.tarball.invalid_name", package=package, project=project)
        return _error("invalid package name", status.HTTP_400_BAD_REQUEST)
    _log.info("npm.tarball.request", package=package, filename=filename, project=project)
    try:
        tarball = await resolve_tarball(proxy.client, package, filename, proxy.settings)
        download = await open_tarball(proxy.client, tarball, proxy.settings)
    except _HANDLED_ERRORS as error:
        return _upstream_error(
            error, "npm.tarball.upstream_error", package=package, filename=filename
        )
    _log.info("npm.tarball.streaming", package=package, filename=filename)
    # Sin Content-Length a propósito: con la respuesta por chunks, cortar el stream
    # cuando el hash no coincide deja al cliente con un cuerpo incompleto que rechaza.
    return StreamingResponse(
        download.iter_verified(),
        media_type="application/octet-stream",
        background=BackgroundTask(download.aclose),
    )


@router.get("/{package}/-/{filename}")
async def get_tarball(package: str, filename: str, proxy: ProxyRequestDep) -> Response:
    """Sirve un tarball de un paquete sin scope, verificando su integridad."""
    return await _serve_tarball(package, filename, proxy)


@router.get("/{scope}/{name}/-/{filename}")
async def get_scoped_tarball(
    scope: str,
    name: str,
    filename: str,
    proxy: ProxyRequestDep,
) -> Response:
    """Sirve un tarball de un paquete con scope, verificando su integridad."""
    return await _serve_tarball(f"{scope}/{name}", filename, proxy)
