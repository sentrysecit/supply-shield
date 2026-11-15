"""Rutas HTTP del espejo de PyPI.

Con ``index-url`` apuntando a este servicio, pip y uv piden el índice simple en
``/simple/<nombre>/`` (HTML o JSON según ``Accept``) y descargan los artefactos
por las rutas reescritas ``/packages/<ruta>``. Todavía no aplica bloqueos: eso es
la tarea D1-3.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from supply_shield.proxy.download import stream_upstream
from supply_shield.proxy.pypi.accept import JSON_MEDIA_TYPE, choose_index_format
from supply_shield.proxy.pypi.models import IndexFormat
from supply_shield.proxy.pypi.registry import ProjectNotFoundError, PyPIRegistry
from supply_shield.proxy.pypi.render import render_html, render_json
from supply_shield.proxy.pypi.urls import upstream_file_url
from supply_shield.proxy.upstream import UpstreamRequestError

router = APIRouter(tags=["proxy: pypi"])

_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_NOT_FOUND = 404


def get_pypi_registry(request: Request) -> PyPIRegistry:
    """Dependencia que devuelve el cliente del espejo desde el estado de la app."""
    registry: object = getattr(request.app.state, "pypi_registry", None)
    if not isinstance(registry, PyPIRegistry):
        msg = "PyPI registry not initialized"
        raise RuntimeError(msg)
    return registry


@router.get("/simple/{project}/")
async def simple_index(
    request: Request,
    project: str,
    registry: Annotated[PyPIRegistry, Depends(get_pypi_registry)],
    accept: Annotated[str | None, Header()] = None,
) -> Response:
    """Sirve el índice simple del proyecto en HTML (PEP 503) o JSON (PEP 691)."""
    try:
        parsed = await registry.fetch_index(project)
    except ProjectNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"package {project!r} not found upstream",
        ) from error
    except UpstreamRequestError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="upstream registry error",
        ) from error
    base_url = str(request.base_url)
    if choose_index_format(accept) is IndexFormat.JSON:
        return JSONResponse(render_json(parsed, base_url), media_type=JSON_MEDIA_TYPE)
    return HTMLResponse(render_html(parsed, base_url))


@router.get("/simple/{project}")
async def simple_index_redirect(project: str) -> RedirectResponse:
    """Redirige a la forma canónica con barra final que espera pip."""
    return RedirectResponse(
        url=f"/simple/{project}/", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.get("/packages/{path:path}")
async def download_artifact(
    path: str,
    registry: Annotated[PyPIRegistry, Depends(get_pypi_registry)],
    sha256: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Reenvía el artefacto upstream byte a byte, verificando su SHA-256 si llega."""
    if sha256 is not None and _HEX256.fullmatch(sha256) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sha256 must be a 64-character lowercase hex digest",
        )
    stream = stream_upstream(
        registry.client,
        upstream_file_url(f"packages/{path}"),
        sha256,
        registry.settings,
        algorithm="sha256",
    )
    return StreamingResponse(stream, media_type="application/octet-stream")
