"""Rutas HTTP del espejo de PyPI, montadas bajo ``/pypi`` (ADR 0013).

Con ``index-url`` apuntando a ``<proxy>/pypi/simple``, pip y uv piden el índice en
``/pypi/simple/<nombre>/`` (HTML o JSON según ``Accept``) y descargan los artefactos
por las rutas reescritas ``/pypi/packages/...``. Todas las rutas exigen el token de
proyecto (ADR 0006). Sin barra final, Starlette redirige a la forma canónica
(``redirect_slashes``), así que aquí no se construye ninguna URL de redirección.
El bloqueo por política es la tarea D1-3.
"""

import re
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from supply_shield.api.auth import get_request_context
from supply_shield.core.request_context import RequestContext
from supply_shield.proxy.download import open_upstream_download
from supply_shield.proxy.pypi.accept import JSON_MEDIA_TYPE, choose_index_format
from supply_shield.proxy.pypi.models import IndexFormat
from supply_shield.proxy.pypi.registry import ProjectNotFoundError, PyPIRegistry
from supply_shield.proxy.pypi.render import render_html, render_json
from supply_shield.proxy.pypi.urls import upstream_file_url
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    UpstreamTooLargeError,
)

router = APIRouter(prefix="/pypi", tags=["pypi"])
_log = structlog.get_logger(__name__)

_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_METADATA_SUFFIX = ".metadata"

# Cómo responde el proxy a cada error del upstream: status y mensaje para el cliente.
_UPSTREAM_ERRORS: tuple[tuple[type[Exception], int, str], ...] = (
    (UpstreamHostError, status.HTTP_403_FORBIDDEN, "upstream host not allowed"),
    (UpstreamTooLargeError, status.HTTP_502_BAD_GATEWAY, "upstream response too large"),
    (UpstreamRequestError, status.HTTP_502_BAD_GATEWAY, "upstream registry error"),
)
_HANDLED_ERRORS: tuple[type[Exception], ...] = tuple(error for error, _, _ in _UPSTREAM_ERRORS)

AuthDep = Annotated[RequestContext, Depends(get_request_context)]


def get_pypi_registry(request: Request) -> PyPIRegistry:
    """Dependencia que devuelve el cliente del espejo desde el estado de la app."""
    registry: object = getattr(request.app.state, "pypi_registry", None)
    if not isinstance(registry, PyPIRegistry):
        msg = "PyPI registry not initialized"
        raise RuntimeError(msg)
    return registry


RegistryDep = Annotated[PyPIRegistry, Depends(get_pypi_registry)]


def _http_error(error: Exception, event: str, **fields: str) -> HTTPException:
    """Registra un error del upstream y lo traduce a su respuesta HTTP."""
    for error_type, status_code, message in _UPSTREAM_ERRORS:
        if isinstance(error, error_type):
            _log.warning(event, reason=type(error).__name__, status=status_code, **fields)
            return HTTPException(status_code=status_code, detail=message)
    raise error


@router.get("/simple/{project}/")
async def simple_index(
    request: Request,
    project: str,
    registry: RegistryDep,
    context: AuthDep,
) -> Response:
    """Sirve el índice simple del proyecto en HTML (PEP 503) o JSON (PEP 691)."""
    _log.info("pypi.index.request", package=project, project=context.project)
    try:
        parsed = await registry.fetch_index(project)
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"package {project!r} not found upstream",
        ) from error
    except _HANDLED_ERRORS as error:
        raise _http_error(error, "pypi.index.upstream_error", package=project) from error
    base_url = registry.settings.proxy_base_url
    if choose_index_format(request.headers.get("accept")) is IndexFormat.JSON:
        return JSONResponse(render_json(parsed, base_url), media_type=JSON_MEDIA_TYPE)
    return HTMLResponse(render_html(parsed, base_url))


async def _serve_file(
    registry: PyPIRegistry, path: str, expected_sha256: str | None, context: RequestContext
) -> StreamingResponse:
    """Abre la descarga upstream, la valida y la reenvía byte a byte.

    La metadata PEP 658 (``.metadata``) no se verifica con el hash del archivo:
    es otro contenido y el cliente la comprueba con el hash de ``data-core-metadata``.
    """
    if ".." in path.split("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid file path")
    if path.endswith(_METADATA_SUFFIX):
        expected_sha256 = None
    _log.info("pypi.file.request", path=path, project=context.project)
    try:
        download = await open_upstream_download(
            registry.client,
            upstream_file_url(f"packages/{path}"),
            expected_sha256,
            registry.settings,
            algorithm="sha256",
        )
    except _HANDLED_ERRORS as error:
        raise _http_error(error, "pypi.file.upstream_error", path=path) from error
    # Sin Content-Length a propósito: cortar el stream cuando el hash no coincide deja
    # al cliente con un cuerpo incompleto que rechaza.
    return StreamingResponse(
        download.iter_verified(),
        media_type="application/octet-stream",
        background=BackgroundTask(download.aclose),
    )


@router.get("/packages/sha256/{digest}/{path:path}")
async def download_verified(
    digest: str, path: str, registry: RegistryDep, context: AuthDep
) -> StreamingResponse:
    """Reenvía el artefacto verificando su SHA-256 contra el de la ruta."""
    if _HEX256.fullmatch(digest) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sha256 must be a 64-character lowercase hex digest",
        )
    return await _serve_file(registry, path, digest, context)


@router.get("/packages/{path:path}")
async def download_artifact(
    path: str, registry: RegistryDep, context: AuthDep
) -> StreamingResponse:
    """Reenvía el artefacto upstream byte a byte, sin verificar (el índice no trajo hash)."""
    return await _serve_file(registry, path, None, context)
