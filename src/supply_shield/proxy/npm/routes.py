"""Rutas FastAPI del espejo del registro npm (packuments y tarballs)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from supply_shield.config import Settings, get_settings
from supply_shield.proxy.npm.packument import decode_package_name, fetch_packument
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    build_upstream_client,
)

router = APIRouter(prefix="/npm", tags=["npm"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _serve_packument(
    package: str,
    request: Request,
    settings: Settings,
) -> Response:
    """Lógica compartida para servir el packument de un paquete (scoped o no)."""
    decoded = decode_package_name(package)
    accept = request.headers.get("accept")
    try:
        async with build_upstream_client(settings) as client:
            upstream_response = await fetch_packument(client, decoded, settings, accept=accept)
    except UpstreamHostError:
        return JSONResponse({"error": "upstream host not allowed"}, status_code=403)
    except UpstreamRequestError:
        return JSONResponse({"error": "upstream unavailable"}, status_code=502)

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )


@router.get("/{package}")
async def get_packument(
    package: str,
    request: Request,
    settings: SettingsDep,
) -> Response:
    """Sirve el packument de un paquete npm sin scope (p. ej. `express`)."""
    return await _serve_packument(package, request, settings)


@router.get("/{scope}/{name}")
async def get_scoped_packument(
    scope: str,
    name: str,
    request: Request,
    settings: SettingsDep,
) -> Response:
    """Sirve el packument de un paquete npm con scope (p. ej. `@types/node`)."""
    return await _serve_packument(f"{scope}/{name}", request, settings)
