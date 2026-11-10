"""Fábrica de la aplicación del servicio Supply-Shield."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from supply_shield import __version__
from supply_shield.analyzers.typosquatting import SeverityThresholds, TyposquattingAnalyzer
from supply_shield.api import admin, auth, health
from supply_shield.api.admin.sync import refresh_loop, safe_refresh
from supply_shield.config import Settings, get_settings
from supply_shield.db import get_session_factory, init_engine
from supply_shield.db.project_tokens import ProjectTokenRepository, SqlProjectTokenRepository
from supply_shield.db.repository import AdminRepository, SqlAdminRepository
from supply_shield.logging import configure_logging
from supply_shield.policy import PolicyHolder, default_source, read_policy_file
from supply_shield.policy.models import Policy

_logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación FastAPI y registra sus routers."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="Supply-Shield", version=__version__, lifespan=_lifespan)
    app.include_router(health.router)
    app.include_router(admin.router)
    _init_token_store(app, settings)
    _init_policy(app, settings)
    _init_admin(app, settings)
    _init_typosquatting(app, settings)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Carga las excepciones de la base al arrancar y las refresca mientras corre.

    Si la base no responde al arrancar, el servicio arranca igual con la política
    del archivo: sin esas excepciones la política solo es más estricta.
    """
    holder: PolicyHolder = app.state.policy_holder
    repository: AdminRepository | None = app.state.admin_repository
    config: admin.AdminConfig = app.state.admin_config
    task: asyncio.Task[None] | None = None
    if repository is not None:
        await safe_refresh(holder, repository)
        task = asyncio.create_task(
            refresh_loop(holder, repository, interval_seconds=config.refresh_seconds),
            name="policy-exceptions-refresh",
        )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _init_typosquatting(app: FastAPI, settings: Settings) -> None:
    """Carga las listas de paquetes populares y construye el detector de typosquatting.

    Si falta una lista o un archivo es inválido, el error detiene el arranque:
    sin listas el detector no protege nada y no debe fallar en silencio.
    """
    thresholds = SeverityThresholds(
        report=settings.typosquatting_report_threshold,
        medium=settings.typosquatting_medium_threshold,
        high=settings.typosquatting_high_threshold,
        critical=settings.typosquatting_critical_threshold,
    )
    analyzer = TyposquattingAnalyzer.from_files(
        settings.popular_packages_dir,
        settings.typosquatting_allowlist_file,
        thresholds=thresholds,
    )
    for ecosystem, index in analyzer.indexes.items():
        _logger.info(
            "typosquatting.loaded",
            ecosystem=ecosystem.value,
            packages=len(index),
            allowed=len(analyzer.allowlist.entries(ecosystem)),
            generated_at=index.popular.generated_at.isoformat(),
        )
    app.state.typosquatting_analyzer = analyzer


def _init_policy(app: FastAPI, settings: Settings) -> None:
    """Carga la política configurada; sin archivo, usa la de fábrica.

    Una política inválida lanza ``PolicyLoadError`` y detiene el arranque: es
    preferible no servir paquetes a servirlos con reglas que nadie escribió. La
    política queda en ``app.state.policy_holder``, que la API de administración
    puede recargar sin reiniciar.
    """
    try:
        policy, source = read_policy_file(settings.policy_file)
    except FileNotFoundError:
        _logger.warning("policy.file_missing", path=str(settings.policy_file))
        policy, source = Policy(), default_source(settings.policy_file)
    _logger.info(
        "policy.loaded",
        projects=len(policy.projects),
        exceptions=len(policy.exceptions),
        sha256=source.sha256,
    )
    app.state.policy_holder = PolicyHolder(policy, source)


def _init_admin(app: FastAPI, settings: Settings) -> None:
    """Prepara la API de administración.

    Sin ``SUPPLY_SHIELD_ADMIN_TOKEN_HASH`` la API queda deshabilitada (503) y no se
    abre la base. Un hash con un formato que ``hash_token`` no produce detiene el
    arranque: es casi seguro que se pegó el token en claro en lugar de su hash.
    """
    secret = settings.admin_token_hash
    token_hash = secret if secret is not None and secret.get_secret_value() else None
    if token_hash is not None and not auth.is_token_hash(token_hash.get_secret_value()):
        msg = (
            "SUPPLY_SHIELD_ADMIN_TOKEN_HASH must be a hash produced by "
            "supply_shield.api.auth.hash_token, not the token itself"
        )
        raise ValueError(msg)
    app.state.admin_config = admin.AdminConfig(
        token_hash=token_hash,
        exception_max_days=settings.admin_exception_max_days,
        policy_file=settings.policy_file,
        refresh_seconds=settings.policy_exceptions_refresh_seconds,
    )
    repository: AdminRepository | None = None
    project_tokens: ProjectTokenRepository | None = None
    if token_hash is None:
        _logger.warning("admin.disabled", reason="SUPPLY_SHIELD_ADMIN_TOKEN_HASH not set")
    else:
        init_engine(settings)
        repository = SqlAdminRepository(get_session_factory())
        project_tokens = SqlProjectTokenRepository(get_session_factory())
        _logger.info("admin.enabled")
    app.state.admin_repository = repository
    app.state.project_token_repository = project_tokens


def _init_token_store(app: FastAPI, settings: Settings) -> None:
    """Carga el almacén de tokens desde el YAML configurado; vacío si no existe."""
    try:
        store = auth.YamlTokenStore.from_file(settings.projects_file)
    except FileNotFoundError:
        store = auth.YamlTokenStore([])
    app.state.token_store = store
