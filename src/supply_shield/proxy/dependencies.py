"""Dependencias FastAPI compartidas por los adaptadores del proxy."""

import httpx
from fastapi import Request


def get_upstream_client(request: Request) -> httpx.AsyncClient:
    """Devuelve el cliente upstream único de la app (ADR 0005).

    Lo crea ``create_app`` y lo cierra el ``lifespan``; así el pool de conexiones y
    ``SUPPLY_SHIELD_UPSTREAM_MAX_CONNECTIONS`` valen para todas las peticiones.
    """
    client: object = getattr(request.app.state, "upstream_client", None)
    if not isinstance(client, httpx.AsyncClient):
        msg = "Upstream client not initialized"
        raise RuntimeError(msg)
    return client
