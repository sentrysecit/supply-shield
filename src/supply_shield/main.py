"""Fábrica de la aplicación del servicio Supply-Shield."""

from fastapi import FastAPI

from supply_shield import __version__
from supply_shield.api import auth, health
from supply_shield.config import Settings, get_settings
from supply_shield.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI y registra sus routers."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="Supply-Shield", version=__version__)
    app.include_router(health.router)
    _init_token_store(app, settings)
    return app


def _init_token_store(app: FastAPI, settings: Settings) -> None:
    """Carga el almacén de tokens desde el YAML configurado; vacío si no existe."""
    try:
        store = auth.YamlTokenStore.from_file(settings.projects_file)
    except FileNotFoundError:
        store = auth.YamlTokenStore([])
    app.state.token_store = store
