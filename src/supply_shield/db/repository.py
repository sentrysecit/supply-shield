"""Acceso a decisiones, SBOMs y excepciones para la API de administración (D4-5).

Los modelos de este módulo son la frontera entre la base de datos y el resto del
código: la API no ve filas de SQLAlchemy y la base no ve modelos de la API. El
protocolo ``AdminRepository`` permite cambiar el almacenamiento sin tocar los
endpoints; ``SqlAdminRepository`` es la implementación sobre SQLAlchemy async.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supply_shield.core.enums import DecisionAction, Ecosystem
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext
from supply_shield.db import models
from supply_shield.policy.models import PolicyException
from supply_shield.policy.rules import Rule

_logger = structlog.get_logger(__name__)

Clock = Callable[[], datetime]

JsonObject = dict[str, object]


def utc_now() -> datetime:
    """Hora actual en UTC."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Devuelve ``value`` en UTC; una fecha sin zona se toma como UTC (así la guarda SQLite)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# --- Errores -------------------------------------------------------------------------


class ExceptionNotFoundError(LookupError):
    """No existe una excepción con ese id."""


class ExceptionAlreadyRevokedError(ValueError):
    """La excepción ya estaba revocada."""


# --- Decisiones ----------------------------------------------------------------------


class DecisionRecord(BaseModel):
    """Decisión guardada, tal como la ve la auditoría."""

    model_config = FROZEN

    id: UUID
    project: str
    build_id: str | None
    package: PackageRef
    action: DecisionAction
    reasons: tuple[str, ...]
    findings: tuple[Finding, ...]
    degraded: bool
    created_at: datetime


class DecisionQuery(BaseModel):
    """Filtros para listar decisiones. ``package`` ya viene normalizado."""

    model_config = FROZEN

    project: str | None = None
    build_id: str | None = None
    ecosystem: Ecosystem | None = None
    package: str | None = None
    version: str | None = None
    action: DecisionAction | None = None
    degraded: bool | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=50, ge=1)
    offset: int = Field(default=0, ge=0)


# --- SBOMs ---------------------------------------------------------------------------


class SbomSummary(BaseModel):
    """Datos de un SBOM guardado, sin el documento."""

    model_config = FROZEN

    id: UUID
    project: str
    build_id: str | None
    created_at: datetime


class SbomDocument(SbomSummary):
    """SBOM guardado con su documento CycloneDX."""

    document: JsonObject


class SbomQuery(BaseModel):
    """Filtros para listar SBOMs."""

    model_config = FROZEN

    project: str | None = None
    build_id: str | None = None
    limit: int = Field(default=50, ge=1)
    offset: int = Field(default=0, ge=0)


# --- Excepciones ---------------------------------------------------------------------


class ExceptionStatus(StrEnum):
    """Estado de una excepción en una fecha dada."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ExceptionDraft(BaseModel):
    """Excepción que se va a crear. ``project`` en ``None`` cubre todos los proyectos."""

    model_config = FROZEN

    project: str | None = None
    package: PackageRef | None = None
    advisory: str | None = None
    rule: Rule | None = None
    justification: str
    author: str
    expires: date

    def to_policy_exception(self) -> PolicyException:
        """La misma excepción con el modelo que entiende el motor de política."""
        return PolicyException(
            package=self.package,
            advisory=self.advisory,
            rule=self.rule,
            projects=() if self.project is None else (self.project,),
            justification=self.justification,
            author=self.author,
            expires=self.expires,
        )


class ExceptionRecord(ExceptionDraft):
    """Excepción guardada, con su id y su historial."""

    id: UUID
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None

    def status(self, today: date) -> ExceptionStatus:
        """Estado de la excepción en ``today`` (UTC)."""
        if self.revoked_at is not None:
            return ExceptionStatus.REVOKED
        if today <= self.expires:
            return ExceptionStatus.ACTIVE
        return ExceptionStatus.EXPIRED


class ExceptionQuery(BaseModel):
    """Filtros para listar excepciones. ``status`` se evalúa en ``today``."""

    model_config = FROZEN

    today: date
    project: str | None = None
    status: ExceptionStatus | None = None
    ecosystem: Ecosystem | None = None
    package: str | None = None
    advisory: str | None = None
    limit: int = Field(default=50, ge=1)
    offset: int = Field(default=0, ge=0)


class ExceptionRevocation(BaseModel):
    """Quién revoca una excepción y por qué."""

    model_config = FROZEN

    revoked_by: str
    reason: str


# --- Contrato ------------------------------------------------------------------------


class AdminRepository(Protocol):
    """Almacenamiento que usa la API de administración."""

    async def record_decision(
        self, package: PackageRef, context: RequestContext, decision: PolicyDecision
    ) -> DecisionRecord:
        """Guarda la decisión tomada para ``package`` en la petición ``context``."""
        ...

    async def list_decisions(self, query: DecisionQuery) -> list[DecisionRecord]:
        """Decisiones que cumplen ``query``, de la más reciente a la más antigua."""
        ...

    async def record_sbom(
        self, project: str, build_id: str | None, document: JsonObject
    ) -> SbomSummary:
        """Guarda un SBOM CycloneDX de ``project`` (y de ``build_id``, si lo hay)."""
        ...

    async def list_sboms(self, query: SbomQuery) -> list[SbomSummary]:
        """SBOMs que cumplen ``query``, del más reciente al más antiguo."""
        ...

    async def get_sbom(self, sbom_id: UUID) -> SbomDocument | None:
        """SBOM con ese id, o ``None``."""
        ...

    async def create_exception(self, draft: ExceptionDraft) -> ExceptionRecord:
        """Guarda una excepción nueva."""
        ...

    async def get_exception(self, exception_id: UUID) -> ExceptionRecord | None:
        """Excepción con ese id, o ``None``."""
        ...

    async def list_exceptions(self, query: ExceptionQuery) -> list[ExceptionRecord]:
        """Excepciones que cumplen ``query``, de la más reciente a la más antigua."""
        ...

    async def revoke_exception(
        self, exception_id: UUID, revocation: ExceptionRevocation
    ) -> ExceptionRecord:
        """Marca la excepción como revocada; no la borra."""
        ...

    async def policy_exceptions(self, not_before: date) -> list[ExceptionRecord]:
        """Excepciones sin revocar que vencen en ``not_before`` o después."""
        ...


# --- Implementación SQL --------------------------------------------------------------


class SqlAdminRepository:
    """``AdminRepository`` sobre SQLAlchemy async (PostgreSQL en producción)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock

    # Decisiones

    async def record_decision(
        self, package: PackageRef, context: RequestContext, decision: PolicyDecision
    ) -> DecisionRecord:
        """Guarda la decisión tomada para ``package`` en la petición ``context``."""
        project_id = await self._ensure_project(context.project)
        row = models.Decision(
            project_id=project_id,
            package_ecosystem=package.ecosystem.value,
            package_name=package.name,
            package_version=package.version,
            build_id=context.build_id,
            action=decision.action.value,
            reasons=list(decision.reasons),
            findings=[finding.model_dump(mode="json") for finding in decision.findings],
            degraded=decision.degraded,
            created_at=self._clock(),
        )
        async with self._sessions() as session:
            session.add(row)
            await session.flush()
            record = _decision_record(row, context.project)
            await session.commit()
        return record

    async def list_decisions(self, query: DecisionQuery) -> list[DecisionRecord]:
        """Decisiones que cumplen ``query``, de la más reciente a la más antigua."""
        statement = select(models.Decision, models.Project.name).join(models.Project)
        statement = _filter_decisions(statement, query)
        statement = (
            statement.order_by(models.Decision.created_at.desc(), models.Decision.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [_decision_record(row, project) for row, project in rows]

    # SBOMs

    async def record_sbom(
        self, project: str, build_id: str | None, document: JsonObject
    ) -> SbomSummary:
        """Guarda un SBOM CycloneDX de ``project`` (y de ``build_id``, si lo hay)."""
        project_id = await self._ensure_project(project)
        row = models.Sbom(
            project_id=project_id,
            build_id=build_id,
            sbom_json=dict(document),
            created_at=self._clock(),
        )
        async with self._sessions() as session:
            session.add(row)
            await session.flush()
            summary = SbomSummary(
                id=row.id, project=project, build_id=build_id, created_at=as_utc(row.created_at)
            )
            await session.commit()
        return summary

    async def list_sboms(self, query: SbomQuery) -> list[SbomSummary]:
        """SBOMs que cumplen ``query``, del más reciente al más antiguo.

        No carga los documentos: pueden pesar varios megabytes.
        """
        statement = select(
            models.Sbom.id, models.Project.name, models.Sbom.build_id, models.Sbom.created_at
        ).join(models.Project)
        if query.project is not None:
            statement = statement.where(models.Project.name == query.project)
        if query.build_id is not None:
            statement = statement.where(models.Sbom.build_id == query.build_id)
        statement = (
            statement.order_by(models.Sbom.created_at.desc(), models.Sbom.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            SbomSummary(id=sbom_id, project=name, build_id=build, created_at=as_utc(created))
            for sbom_id, name, build, created in rows
        ]

    async def get_sbom(self, sbom_id: UUID) -> SbomDocument | None:
        """SBOM con ese id, o ``None``."""
        statement = (
            select(models.Sbom, models.Project.name)
            .join(models.Project)
            .where(models.Sbom.id == sbom_id)
        )
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
        if found is None:
            return None
        row, project = found
        return SbomDocument(
            id=row.id,
            project=project,
            build_id=row.build_id,
            created_at=as_utc(row.created_at),
            document=dict(row.sbom_json),
        )

    # Excepciones

    async def create_exception(self, draft: ExceptionDraft) -> ExceptionRecord:
        """Guarda una excepción nueva."""
        project_id = None if draft.project is None else await self._ensure_project(draft.project)
        package = draft.package
        row = models.PolicyException(
            project_id=project_id,
            package_ecosystem=None if package is None else package.ecosystem.value,
            package_name=None if package is None else package.name,
            package_version=None if package is None else package.version,
            advisory_id=draft.advisory,
            rule=None if draft.rule is None else draft.rule.value,
            justification=draft.justification,
            author=draft.author,
            expires_on=draft.expires,
            created_at=self._clock(),
        )
        async with self._sessions() as session:
            session.add(row)
            await session.flush()
            record = _exception_record(row, draft.project)
            await session.commit()
        return record

    async def get_exception(self, exception_id: UUID) -> ExceptionRecord | None:
        """Excepción con ese id, o ``None``."""
        statement = _exceptions_select().where(models.PolicyException.id == exception_id)
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
        return None if found is None else _exception_record(*found)

    async def list_exceptions(self, query: ExceptionQuery) -> list[ExceptionRecord]:
        """Excepciones que cumplen ``query``, de la más reciente a la más antigua."""
        table = models.PolicyException
        statement = _exceptions_select()
        if query.project is not None:
            statement = statement.where(models.Project.name == query.project)
        if query.ecosystem is not None:
            statement = statement.where(table.package_ecosystem == query.ecosystem.value)
        if query.package is not None:
            statement = statement.where(table.package_name == query.package)
        if query.advisory is not None:
            statement = statement.where(table.advisory_id == query.advisory)
        if query.status is ExceptionStatus.REVOKED:
            statement = statement.where(table.revoked_at.is_not(None))
        elif query.status is ExceptionStatus.ACTIVE:
            statement = statement.where(table.revoked_at.is_(None), table.expires_on >= query.today)
        elif query.status is ExceptionStatus.EXPIRED:
            statement = statement.where(table.revoked_at.is_(None), table.expires_on < query.today)
        statement = (
            statement.order_by(table.created_at.desc(), table.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [_exception_record(row, project) for row, project in rows]

    async def revoke_exception(
        self, exception_id: UUID, revocation: ExceptionRevocation
    ) -> ExceptionRecord:
        """Marca la excepción como revocada; no la borra.

        La fila se bloquea (``FOR UPDATE``) para que dos revocaciones simultáneas no
        se pisen: la segunda ve la primera y falla con ``ExceptionAlreadyRevokedError``.
        """
        statement = (
            _exceptions_select()
            .where(models.PolicyException.id == exception_id)
            .with_for_update(of=models.PolicyException)
        )
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
            if found is None:
                raise ExceptionNotFoundError(str(exception_id))
            row, project = found
            if row.revoked_at is not None:
                raise ExceptionAlreadyRevokedError(str(exception_id))
            row.revoked_at = self._clock()
            row.revoked_by = revocation.revoked_by
            row.revoke_reason = revocation.reason
            await session.flush()
            record = _exception_record(row, project)
            await session.commit()
        return record

    async def policy_exceptions(self, not_before: date) -> list[ExceptionRecord]:
        """Excepciones sin revocar que vencen en ``not_before`` o después."""
        table = models.PolicyException
        statement = (
            _exceptions_select()
            .where(table.revoked_at.is_(None), table.expires_on >= not_before)
            .order_by(table.created_at, table.id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        records: list[ExceptionRecord] = []
        for row, project in rows:
            try:
                records.append(_exception_record(row, project))
            except ValueError:
                # Una fila corrupta (p. ej. editada a mano) no debe impedir cargar las
                # demás. Descartarla solo vuelve más estricta la política.
                _logger.error("db.exception_invalid_row", exception_id=str(row.id))
        return records

    # Proyectos

    async def _ensure_project(self, name: str) -> UUID:
        return await ensure_project(self._sessions, name, clock=self._clock)


async def ensure_project(
    sessions: async_sessionmaker[AsyncSession], name: str, *, clock: Clock = utc_now
) -> UUID:
    """Id del proyecto ``name``; crea la fila si todavía no existe.

    Los proyectos se definen en ``config/projects.yaml``; la base solo guarda una
    fila por nombre para colgar de ella decisiones, SBOMs, excepciones y tokens.
    """
    lookup = select(models.Project.id).where(models.Project.name == name)
    async with sessions() as session:
        found = await session.scalar(lookup)
        if found is not None:
            return found
        project = models.Project(name=name, created_at=clock())
        session.add(project)
        try:
            await session.flush()
            project_id = project.id
            await session.commit()
        except IntegrityError:
            # Otra petición lo creó entre la consulta y el INSERT.
            await session.rollback()
            found = await session.scalar(lookup)
            if found is None:
                raise
            return found
        return project_id


def _filter_decisions(
    statement: Select[models.Decision, str], query: DecisionQuery
) -> Select[models.Decision, str]:
    table = models.Decision
    conditions = []
    if query.project is not None:
        conditions.append(models.Project.name == query.project)
    if query.build_id is not None:
        conditions.append(table.build_id == query.build_id)
    if query.ecosystem is not None:
        conditions.append(table.package_ecosystem == query.ecosystem.value)
    if query.package is not None:
        conditions.append(table.package_name == query.package)
    if query.version is not None:
        conditions.append(table.package_version == query.version)
    if query.action is not None:
        conditions.append(table.action == query.action.value)
    if query.degraded is not None:
        conditions.append(table.degraded.is_(query.degraded))
    if query.since is not None:
        conditions.append(table.created_at >= as_utc(query.since))
    if query.until is not None:
        conditions.append(table.created_at < as_utc(query.until))
    return statement.where(*conditions)


def _exceptions_select() -> Select[models.PolicyException, str | None]:
    return select(models.PolicyException, models.Project.name).outerjoin(models.Project)


def _decision_record(row: models.Decision, project: str) -> DecisionRecord:
    findings: Sequence[object] = row.findings
    return DecisionRecord(
        id=row.id,
        project=project,
        build_id=row.build_id,
        package=PackageRef(
            ecosystem=Ecosystem(row.package_ecosystem),
            name=row.package_name,
            version=row.package_version,
        ),
        action=DecisionAction(row.action),
        reasons=tuple(row.reasons),
        findings=tuple(Finding.model_validate(finding) for finding in findings),
        degraded=row.degraded,
        created_at=as_utc(row.created_at),
    )


def _exception_record(row: models.PolicyException, project: str | None) -> ExceptionRecord:
    package = None
    if row.package_ecosystem is not None and row.package_name is not None:
        package = PackageRef(
            ecosystem=Ecosystem(row.package_ecosystem),
            name=row.package_name,
            version=row.package_version,
        )
    return ExceptionRecord(
        id=row.id,
        project=project,
        package=package,
        advisory=row.advisory_id,
        rule=None if row.rule is None else Rule(row.rule),
        justification=row.justification,
        author=row.author,
        expires=row.expires_on,
        created_at=as_utc(row.created_at),
        revoked_at=None if row.revoked_at is None else as_utc(row.revoked_at),
        revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason,
    )
