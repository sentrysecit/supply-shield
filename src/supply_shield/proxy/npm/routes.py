"""Rutas FastAPI del espejo del registro npm (packuments y tarballs)."""

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from supply_shield.config import Settings, get_settings
from supply_shield.proxy.npm.packument import (
    decode_package_name,
    fetch_packument,
    rewrite_tarballs,
    validate_package_name,
)
from supply_shield.proxy.npm.tarball import resolve_tarball, stream_tarball
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    build_upstream_client,
)

router = APIRouter(prefix="/npm", tags=["npm"])
_log = structlog.get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]
_OK_STATUS = 200


async def _serve_packument(
    package: str,
    request: Request,
    settings: Settings,
) -> Response:
    """Lógica compartida para servir el packument de un paquete (scoped o no)."""
    decoded = decode_package_name(package)
    try:
        validate_package_name(decoded)
    except ValueError:
        _log.warning("npm.packument.invalid_name", package=decoded)
        return JSONResponse({"error": "invalid package name"}, status_code=400)
    accept = request.headers.get("accept")
    _log.info("npm.packument.request", package=decoded, accept=accept)
    try:
        async with build_upstream_client(settings) as client:
            upstream_response = await fetch_packument(client, decoded, settings, accept=accept)
    except UpstreamHostError:
        _log.warning("npm.packument.host_blocked", package=decoded)
        return JSONResponse({"error": "upstream host not allowed"}, status_code=403)
    except UpstreamRequestError:
        _log.warning("npm.packument.upstream_error", package=decoded)
        return JSONResponse({"error": "upstream unavailable"}, status_code=502)

    media_type = upstream_response.headers.get("content-type", "application/json")
    if upstream_response.status_code == _OK_STATUS:
        packument: dict[str, Any] = upstream_response.json()
        rewrite_tarballs(packument, settings.proxy_base_url)
        _log.info("npm.packument.served", package=decoded, status=_OK_STATUS)
        return JSONResponse(content=packument, media_type=media_type)

    _log.info(
        "npm.packument.upstream_status",
        package=decoded,
        status=upstream_response.status_code,
    )
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=media_type,
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


async def _serve_tarball(decoded: str, filename: str, settings: Settings) -> Response:
    """Lógica compartida para resolver y servir un tarball (scoped o no)."""
    try:
        validate_package_name(decoded)
    except ValueError:
        _log.warning("npm.tarball.invalid_name", package=decoded)
        return JSONResponse({"error": "invalid package name"}, status_code=400)
    _log.info("npm.tarball.request", package=decoded, filename=filename)
    try:
        tarball_url, expected_hex = await resolve_tarball(decoded, filename, settings)
    except UpstreamHostError:
        _log.warning("npm.tarball.host_blocked", package=decoded)
        return JSONResponse({"error": "upstream host not allowed"}, status_code=403)
    except UpstreamRequestError:
        _log.warning("npm.tarball.not_found", package=decoded, filename=filename)
        return JSONResponse({"error": "tarball not found"}, status_code=502)
    _log.info("npm.tarball.streaming", package=decoded, filename=filename)
    return StreamingResponse(
        stream_tarball(tarball_url, expected_hex, settings),
        media_type="application/octet-stream",
    )


@router.get("/{package}/-/{filename}")
async def get_tarball(
    package: str,
    filename: str,
    settings: SettingsDep,
) -> Response:
    """Sirve un tarball de un paquete sin scope, verificando su integridad."""
    return await _serve_tarball(decode_package_name(package), filename, settings)


@router.get("/{scope}/{name}/-/{filename}")
async def get_scoped_tarball(
    scope: str,
    name: str,
    filename: str,
    settings: SettingsDep,
) -> Response:
    """Sirve un tarball de un paquete con scope, verificando su integridad."""
    return await _serve_tarball(f"{scope}/{name}", filename, settings)
