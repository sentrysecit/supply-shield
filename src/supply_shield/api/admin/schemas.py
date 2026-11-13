"""Modelos de petición y respuesta de la API de administración (definen el OpenAPI)."""

from datetime import date, datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from supply_shield.api.auth import project_token_hint
from supply_shield.core.enums import DecisionAction, Ecosystem
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef
from supply_shield.db.project_tokens import ProjectTokenRecord, ProjectTokenStatus
from supply_shield.db.repository import (
    DecisionRecord,
    ExceptionRecord,
    ExceptionStatus,
    SbomSummary,
)
from supply_shield.policy.holder import PolicyHolder, PolicySource
from supply_shield.policy.models import Policy
from supply_shield.policy.rules import Rule

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
TokenName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

_MAX_NAME = 255
_MAX_VERSION = 100


class ErrorDetail(BaseModel):
    """Cuerpo de un error."""

    detail: str


class Page[T](BaseModel):
    """Una página de resultados. ``next_offset`` es ``null`` en la última."""

    items: list[T]
    limit: int
    offset: int
    next_offset: int | None


class PackageView(BaseModel):
    """Paquete con su nombre normalizado y su purl."""

    ecosystem: Ecosystem
    name: str
    version: str | None
    purl: str

    @classmethod
    def of(cls, package: PackageRef) -> Self:
        """Vista de una ``PackageRef``."""
        return cls(
            ecosystem=package.ecosystem,
            name=package.name,
            version=package.version,
            purl=package.purl,
        )


# --- Decisiones ----------------------------------------------------------------------


class DecisionView(BaseModel):
    """Decisión guardada por el proxy."""

    id: UUID
    project: str
    build_id: str | None
    package: PackageView
    action: DecisionAction
    reasons: list[str]
    findings: list[Finding]
    degraded: bool
    created_at: datetime

    @classmethod
    def of(cls, record: DecisionRecord) -> Self:
        """Vista de una decisión guardada."""
        return cls(
            id=record.id,
            project=record.project,
            build_id=record.build_id,
            package=PackageView.of(record.package),
            action=record.action,
            reasons=list(record.reasons),
            findings=list(record.findings),
            degraded=record.degraded,
            created_at=record.created_at,
        )


# --- SBOMs ---------------------------------------------------------------------------


class SbomView(BaseModel):
    """SBOM guardado, sin el documento. ``href`` lleva al CycloneDX completo."""

    id: UUID
    project: str
    build_id: str | None
    created_at: datetime
    href: str

    @classmethod
    def of(cls, summary: SbomSummary, href: str) -> Self:
        """Vista de un SBOM guardado."""
        return cls(
            id=summary.id,
            project=summary.project,
            build_id=summary.build_id,
            created_at=summary.created_at,
            href=href,
        )


# --- Excepciones ---------------------------------------------------------------------


class ExceptionCreate(BaseModel):
    """Excepción nueva.

    Debe nombrar un ``package``, un ``advisory`` o ambos. Una excepción por paquete
    sin versión y sin advisory cubriría cualquier hallazgo futuro de ese paquete
    (incluido malware), así que exige ``rule`` y no puede ser ``known_malware``.
    """

    model_config = FROZEN

    project: ShortText | None = Field(
        default=None, description="Proyecto al que se limita. Vacío = todos los proyectos."
    )
    package: PackageRef | None = Field(
        default=None,
        description="Ecosistema, nombre y, opcionalmente, versión. El nombre se normaliza.",
    )
    advisory: ShortText | None = Field(
        default=None, description="Id del advisory (CVE, GHSA, PYSEC, MAL…)."
    )
    rule: Rule | None = Field(default=None, description="Limita la excepción a esta regla.")
    justification: LongText = Field(description="Por qué es aceptable el riesgo.")
    author: ShortText = Field(description="Quién la pide o aprueba.")
    expires: date = Field(description="Último día en que rige, inclusive, en UTC.")

    @model_validator(mode="after")
    def _is_narrow(self) -> Self:
        if self.package is None and self.advisory is None:
            msg = "una excepción debe nombrar un paquete, un advisory o ambos"
            raise ValueError(msg)
        package = self.package
        if package is not None:
            if len(package.name) > _MAX_NAME:
                msg = f"el nombre del paquete no puede pasar de {_MAX_NAME} caracteres"
                raise ValueError(msg)
            if package.version is not None and len(package.version) > _MAX_VERSION:
                msg = f"la versión no puede pasar de {_MAX_VERSION} caracteres"
                raise ValueError(msg)
            if package.version is None and self.advisory is None:
                if self.rule is None:
                    msg = (
                        "una excepción por paquete sin versión ni advisory debe indicar "
                        "la regla que exceptúa"
                    )
                    raise ValueError(msg)
                if self.rule is Rule.KNOWN_MALWARE:
                    msg = "una excepción de known_malware debe indicar la versión o el advisory"
                    raise ValueError(msg)
        return self


class ExceptionRevoke(BaseModel):
    """Quién revoca una excepción y por qué."""

    model_config = FROZEN

    revoked_by: ShortText
    reason: LongText


class ExceptionView(BaseModel):
    """Excepción creada por la API, con su estado a la fecha de la consulta."""

    id: UUID
    project: str | None
    package: PackageView | None
    advisory: str | None
    rule: Rule | None
    justification: str
    author: str
    expires: date
    status: ExceptionStatus
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_reason: str | None

    @classmethod
    def of(cls, record: ExceptionRecord, today: date) -> Self:
        """Vista de una excepción guardada, con su estado en ``today``."""
        return cls(
            id=record.id,
            project=record.project,
            package=None if record.package is None else PackageView.of(record.package),
            advisory=record.advisory,
            rule=record.rule,
            justification=record.justification,
            author=record.author,
            expires=record.expires,
            status=record.status(today),
            created_at=record.created_at,
            revoked_at=record.revoked_at,
            revoked_by=record.revoked_by,
            revoke_reason=record.revoke_reason,
        )


# --- Tokens de proyecto -------------------------------------------------------------


class ProjectTokenCreate(BaseModel):
    """Token de proyecto nuevo."""

    model_config = FROZEN

    name: TokenName = Field(
        description="Para qué es el token, p. ej. `ci-github` o `laptop-ana`. Sirve para "
        "reconocerlo al revocarlo."
    )
    created_by: ShortText = Field(description="Quién lo pide o aprueba.")
    expires: date | None = Field(
        default=None,
        description="Último día en que vale, inclusive, en UTC. Vacío = no vence.",
    )


class ProjectTokenRevoke(BaseModel):
    """Quién revoca un token y por qué."""

    model_config = FROZEN

    revoked_by: ShortText
    reason: LongText


class ProjectTokenView(BaseModel):
    """Token de proyecto guardado, con su estado en el momento de la consulta.

    Nunca incluye el token ni su hash. ``hint`` es la parte pública del token
    (``ssp_<key_id>_…``): sirve para saber a qué registro corresponde un token.
    """

    id: UUID
    project: str
    name: str
    key_id: str
    hint: str
    created_by: str
    created_at: datetime
    expires: date | None
    status: ProjectTokenStatus
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_reason: str | None

    @classmethod
    def of(cls, record: ProjectTokenRecord, now: datetime) -> Self:
        """Vista de un token guardado, con su estado en ``now``."""
        return cls(
            id=record.id,
            project=record.project,
            name=record.name,
            key_id=record.key_id,
            hint=project_token_hint(record.key_id),
            created_by=record.created_by,
            created_at=record.created_at,
            expires=record.expires,
            status=record.status(now),
            revoked_at=record.revoked_at,
            revoked_by=record.revoked_by,
            revoke_reason=record.revoke_reason,
        )


class ProjectTokenCreated(ProjectTokenView):
    """Token recién creado. ``token`` solo aparece en esta respuesta."""

    token: str = Field(
        description="El token completo. Guárdalo ahora: el servicio solo conserva su hash."
    )


# --- Política ------------------------------------------------------------------------


class PolicyView(BaseModel):
    """Política activa.

    ``policy`` es la del archivo (o la de fábrica), con sus propias excepciones.
    ``api_exceptions`` cuenta las excepciones de la API que el proceso aplica encima.
    ``revision`` sube con cada cambio de la política efectiva.
    """

    source: PolicySource
    revision: int
    api_exceptions: int
    policy: Policy

    @classmethod
    def of(cls, holder: PolicyHolder) -> Self:
        """Vista de la política que guarda ``holder``."""
        return cls(
            source=holder.source,
            revision=holder.revision,
            api_exceptions=len(holder.dynamic_exceptions),
            policy=holder.base,
        )
