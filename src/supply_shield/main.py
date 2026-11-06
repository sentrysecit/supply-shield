"""Fábrica de la aplicación del servicio Supply-Shield."""

import structlog
from fastapi import FastAPI

from supply_shield import __version__
from supply_shield.api import auth, health
from supply_shield.config import Settings, get_settings
from supply_shield.logging import configure_logging
from supply_shield.policy import Policy, load_policy

_logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI y registra sus routers."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="Supply-Shield", version=__version__)
    app.include_router(health.router)
    _init_token_store(app, settings)
    _init_policy(app, settings)
    return app


def _init_policy(app: FastAPI, settings: Settings) -> None:
    """Carga la política configurada; sin archivo, usa la de fábrica.

    Una política inválida lanza ``PolicyLoadError`` y detiene el arranque: es
    preferible no servir paquetes a servirlos con reglas que nadie escribió.
    """
    try:
        policy = load_policy(settings.policy_file)
    except FileNotFoundError:
        _logger.warning("policy.file_missing", path=str(settings.policy_file))
        policy = Policy()
    _logger.info(
        "policy.loaded",
        projects=len(policy.projects),
        exceptions=len(policy.exceptions),
    )
    app.state.policy = policy


def _init_token_store(app: FastAPI, settings: Settings) -> None:
    """Carga el almacén de tokens desde el YAML configurado; vacío si no existe."""
    try:
        store = auth.YamlTokenStore.from_file(settings.projects_file)
    except FileNotFoundError:
        store = auth.YamlTokenStore([])
    app.state.token_store = store
