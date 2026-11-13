"""Tokens de proyecto guardados en la base y gestionados por la API de administración.

La base guarda solo el hash de cada token y su ``key_id`` público (ver
``supply_shield.api.auth.issue_project_token``). Revocar no borra la fila.

La autenticación del proxy todavía lee ``config/projects.yaml``: estos tokens no
abren el proxy hasta que ``TokenStore`` también los lea (ADR 0010).
"""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supply_shield.core.model_config import FROZEN
from supply_shield.db import models
from supply_shield.db.repository import Clock, as_utc, ensure_project, utc_now

_ONE_DAY = timedelta(days=1)
_JUST_BEFORE = timedelta(microseconds=1)


def expiry_instant(expires: date) -> datetime:
    """Primer instante en que deja de valer un token que vence el día ``expires`` (UTC)."""
    return datetime.combine(expires + _ONE_DAY, time(), tzinfo=UTC)


def expiry_day(instant: datetime) -> date:
    """Último día (UTC) en que vale un token que deja de valer en ``instant``."""
    return (as_utc(instant) - _JUST_BEFORE).date()


# --- Errores -------------------------------------------------------------------------


class ProjectTokenNotFoundError(LookupError):
    """No existe un token con ese id en ese proyecto."""


class ProjectTokenAlreadyRevokedError(ValueError):
    """El token ya estaba revocado."""


# --- Modelos -------------------------------------------------------------------------


class ProjectTokenStatus(StrEnum):
    """Estado de un token en un momento dado."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ProjectTokenDraft(BaseModel):
    """Token que se va a guardar. ``expires`` en ``None`` = no vence."""

    model_config = FROZEN

    project: str
    name: str
    key_id: str
    token_hash: str = Field(repr=False)
    created_by: str
    expires: date | None = None


class ProjectTokenRecord(BaseModel):
    """Token guardado, sin su hash."""

    model_config = FROZEN

    id: UUID
    project: str
    name: str
    key_id: str
    created_by: str
    created_at: datetime
    expires: date | None
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None

    def status(self, now: datetime) -> ProjectTokenStatus:
        """Estado del token en ``now``."""
        if self.revoked_at is not None:
            return ProjectTokenStatus.REVOKED
        if self.expires is not None and as_utc(now) >= expiry_instant(self.expires):
            return ProjectTokenStatus.EXPIRED
        return ProjectTokenStatus.ACTIVE


class ProjectTokenQuery(BaseModel):
    """Filtros para listar los tokens de un proyecto. ``status`` se evalúa en ``now``."""

    model_config = FROZEN

    project: str
    now: datetime
    status: ProjectTokenStatus | None = None
    limit: int = Field(default=50, ge=1)
    offset: int = Field(default=0, ge=0)


class ProjectTokenRevocation(BaseModel):
    """Quién revoca un token y por qué."""

    model_config = FROZEN

    revoked_by: str
    reason: str


# --- Contrato ------------------------------------------------------------------------


class ProjectTokenRepository(Protocol):
    """Almacenamiento de los tokens de proyecto."""

    async def create_project_token(self, draft: ProjectTokenDraft) -> ProjectTokenRecord:
        """Guarda un token nuevo."""
        ...

    async def get_project_token(self, project: str, token_id: UUID) -> ProjectTokenRecord | None:
        """Token ``token_id`` de ``project``, o ``None``."""
        ...

    async def list_project_tokens(self, query: ProjectTokenQuery) -> list[ProjectTokenRecord]:
        """Tokens que cumplen ``query``, del más reciente al más antiguo."""
        ...

    async def revoke_project_token(
        self, project: str, token_id: UUID, revocation: ProjectTokenRevocation
    ) -> ProjectTokenRecord:
        """Marca el token como revocado; no lo borra."""
        ...


# --- Implementación SQL --------------------------------------------------------------


class SqlProjectTokenRepository:
    """``ProjectTokenRepository`` sobre SQLAlchemy async (PostgreSQL en producción)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    async def create_project_token(self, draft: ProjectTokenDraft) -> ProjectTokenRecord:
        """Guarda un token nuevo."""
        project_id = await ensure_project(self._sessions, draft.project, clock=self._clock)
        row = models.Token(
            project_id=project_id,
            key_id=draft.key_id,
            name=draft.name,
            token_hash=draft.token_hash,
            created_by=draft.created_by,
            created_at=self._clock(),
            expires_at=None if draft.expires is None else expiry_instant(draft.expires),
        )
        async with self._sessions() as session:
            session.add(row)
            await session.flush()
            record = _token_record(row, draft.project)
            await session.commit()
        return record

    async def get_project_token(self, project: str, token_id: UUID) -> ProjectTokenRecord | None:
        """Token ``token_id`` de ``project``, o ``None``."""
        statement = _tokens_select(project).where(models.Token.id == token_id)
        async with self._sessions() as session:
            row = await session.scalar(statement)
        return None if row is None else _token_record(row, project)

    async def list_project_tokens(self, query: ProjectTokenQuery) -> list[ProjectTokenRecord]:
        """Tokens que cumplen ``query``, del más reciente al más antiguo."""
        table = models.Token
        now = as_utc(query.now)
        statement = _tokens_select(query.project)
        if query.status is ProjectTokenStatus.REVOKED:
            statement = statement.where(table.revoked_at.is_not(None))
        elif query.status is ProjectTokenStatus.ACTIVE:
            statement = statement.where(
                table.revoked_at.is_(None),
                table.expires_at.is_(None) | (table.expires_at > now),
            )
        elif query.status is ProjectTokenStatus.EXPIRED:
            statement = statement.where(table.revoked_at.is_(None), table.expires_at <= now)
        statement = (
            statement.order_by(table.created_at.desc(), table.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_token_record(row, query.project) for row in rows]

    async def revoke_project_token(
        self, project: str, token_id: UUID, revocation: ProjectTokenRevocation
    ) -> ProjectTokenRecord:
        """Marca el token como revocado; no lo borra.

        La fila se bloquea (``FOR UPDATE``) para que dos revocaciones simultáneas no
        se pisen: la segunda ve la primera y falla con ``ProjectTokenAlreadyRevokedError``.
        """
        statement = (
            _tokens_select(project)
            .where(models.Token.id == token_id)
            .with_for_update(of=models.Token)
        )
        async with self._sessions() as session:
            row = await session.scalar(statement)
            if row is None:
                raise ProjectTokenNotFoundError(str(token_id))
            if row.revoked_at is not None:
                raise ProjectTokenAlreadyRevokedError(str(token_id))
            row.revoked_at = self._clock()
            row.revoked_by = revocation.revoked_by
            row.revoke_reason = revocation.reason
            await session.flush()
            record = _token_record(row, project)
            await session.commit()
        return record


def _tokens_select(project: str) -> Select[models.Token]:
    return select(models.Token).join(models.Project).where(models.Project.name == project)


def _token_record(row: models.Token, project: str) -> ProjectTokenRecord:
    return ProjectTokenRecord(
        id=row.id,
        project=project,
        name=row.name,
        key_id=row.key_id,
        created_by=row.created_by,
        created_at=as_utc(row.created_at),
        expires=None if row.expires_at is None else expiry_day(row.expires_at),
        revoked_at=None if row.revoked_at is None else as_utc(row.revoked_at),
        revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason,
    )
