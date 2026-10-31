"""Fábrica de la aplicación del servicio Supply-Shield."""

from fastapi import FastAPI

from supply_shield import __version__
from supply_shield.api import health
from supply_shield.config import Settings, get_settings
from supply_shield.logging import configure_logging
from supply_shield.proxy.npm import routes as npm_routes


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI y registra sus routers."""
    configure_logging(settings or get_settings())

    app = FastAPI(title="Supply-Shield", version=__version__)
    app.include_router(health.router)
    app.include_router(npm_routes.router)
    return app
