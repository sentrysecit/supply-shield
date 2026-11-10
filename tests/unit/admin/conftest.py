"""Fixtures de la API de administración: app con token de admin y base SQLite temporal."""

import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import structlog
import yaml
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from structlog.testing import LogCapture
from structlog.typing import EventDict

from supply_shield.api.admin import exceptions as admin_exceptions
from supply_shield.api.admin import policy as admin_policy
from supply_shield.api.admin import project_tokens as admin_project_tokens
from supply_shield.api.admin import security
from supply_shield.api.admin import sync as admin_sync
from supply_shield.api.admin.dependencies import get_now
from supply_shield.config import Settings
from supply_shield.db.models import Base
from supply_shield.db.project_tokens import SqlProjectTokenRepository
from supply_shield.db.repository import SqlAdminRepository
from supply_shield.main import create_app
from supply_shield.policy.holder import PolicyHolder

ADMIN_TOKEN = "ssa_test_admin_token"  # noqa: S105 - token de prueba
PROJECT_TOKEN = "ss_frontend_test_token"  # noqa: S105 - token de prueba
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
TODAY: date = NOW.date()


def fast_hash(token: str) -> str:
    """Hash con el formato de ``hash_token`` pero pocas iteraciones, para pruebas rápidas.

    ``verify_token`` lee las iteraciones del propio hash, así que lo verifica igual.
    """
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, 1_000, 32)
    return f"pbkdf2_sha256$1000${salt.hex()}${digest.hex()}"


ADMIN_HASH = fast_hash(ADMIN_TOKEN)

POLICY_YAML = """\
version: 1
rules:
  vulnerability:
    mode: block
    severity_threshold: critical
"""


class TickingClock:
    """Reloj que avanza un segundo en cada lectura: da un orden estable a las filas."""

    def __init__(self, start: datetime = NOW) -> None:
        self._next = start

    def __call__(self) -> datetime:
        current = self._next
        self._next += timedelta(seconds=1)
        return current


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Base SQLite en memoria con el esquema del ORM, compartida por todas las sesiones."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def repository(sessions: async_sessionmaker[AsyncSession]) -> SqlAdminRepository:
    """Repositorio SQL sobre la base temporal, con un reloj determinista."""
    return SqlAdminRepository(sessions, clock=TickingClock())


@pytest.fixture
def token_repository(sessions: async_sessionmaker[AsyncSession]) -> SqlProjectTokenRepository:
    """Repositorio de tokens de proyecto sobre la base temporal, con un reloj determinista."""
    return SqlProjectTokenRepository(sessions, clock=TickingClock())


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    """Archivo de política mínimo y válido."""
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY_YAML, encoding="utf-8")
    return path


@pytest.fixture
def projects_file(tmp_path: Path) -> Path:
    """Proyectos ``frontend`` y ``backend`` con tokens de prueba."""
    path = tmp_path / "projects.yaml"
    data = {
        "projects": [
            {"name": "frontend", "token_hash": fast_hash(PROJECT_TOKEN)},
            {"name": "backend", "token_hash": fast_hash("ss_backend_test_token")},
        ]
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


AdminSettingsFactory = Callable[..., Settings]


@pytest.fixture
def admin_settings(policy_file: Path, projects_file: Path) -> AdminSettingsFactory:
    """Construye ajustes con la API de administración habilitada."""

    def build(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "admin_token_hash": SecretStr(ADMIN_HASH),
            "policy_file": policy_file,
            "projects_file": projects_file,
            "database_url": "postgresql+asyncpg://unused:unused@localhost:1/unused",
        }
        values.update(overrides)
        return Settings.model_validate({"_env_file": None, **values})

    return build


@pytest.fixture
def admin_app(
    admin_settings: AdminSettingsFactory,
    repository: SqlAdminRepository,
    token_repository: SqlProjectTokenRepository,
) -> FastAPI:
    """App con la API de administración sobre la base temporal y el reloj fijo."""
    app = create_app(admin_settings())
    app.state.admin_repository = repository
    app.state.project_token_repository = token_repository
    app.dependency_overrides[get_now] = lambda: NOW
    return app


@pytest.fixture
def holder(admin_app: FastAPI) -> PolicyHolder:
    """Política activa de ``admin_app``."""
    value: PolicyHolder = admin_app.state.policy_holder
    return value


def _client_for(app: FastAPI, token: str | None) -> httpx.AsyncClient:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
    )


@pytest.fixture
async def admin(admin_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente autenticado como administrador."""
    async with _client_for(admin_app, ADMIN_TOKEN) as client:
        yield client


@pytest.fixture
async def anonymous(admin_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente sin credenciales."""
    async with _client_for(admin_app, None) as client:
        yield client


@pytest.fixture
def client_for() -> Callable[[FastAPI, str | None], httpx.AsyncClient]:
    """Construye un cliente para una app y un token dados."""
    return _client_for


@pytest.fixture
def admin_logs(monkeypatch: pytest.MonkeyPatch) -> list[EventDict]:
    """Eventos de log de los módulos de administración.

    structlog cachea cada logger en su primer uso, así que ``capture_logs`` no ve
    los que otra prueba ya usó; se reemplazan por uno que solo captura.
    """
    capture = LogCapture()
    for module in (security, admin_policy, admin_exceptions, admin_project_tokens, admin_sync):
        monkeypatch.setattr(module, "_logger", structlog.wrap_logger(None, processors=[capture]))
    return capture.entries
