"""Modelos de base de datos SQLAlchemy 2 async.

Tablas: proyectos, tokens, decisiones, sboms, builds, excepciones.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from supply_shield.config import Settings

# Tipos reutilizables
UUID_PK = Annotated[
    uuid.UUID, mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
]
CreatedAt = Annotated[datetime, mapped_column(DateTime(timezone=True), server_default=func.now())]


class Base(DeclarativeBase):
    """Clase base de todos los modelos ORM."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: UUID(as_uuid=True),
    }


class Project(Base):
    """Proyecto registrado en el firewall."""

    __tablename__ = "projects"

    id: Mapped[UUID_PK]
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[CreatedAt]

    tokens: Mapped[list[Token]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[Decision]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    sboms: Mapped[list[Sbom]] = relationship(back_populates="project", cascade="all, delete-orphan")
    builds: Mapped[list[Build]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[PolicyException]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Token(Base):
    """Token de acceso para un proyecto (guardado como hash)."""

    __tablename__ = "tokens"

    id: Mapped[UUID_PK]
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="tokens")


class Decision(Base):
    """Decisión de política para una versión de paquete en un proyecto."""

    __tablename__ = "decisions"

    id: Mapped[UUID_PK]
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_ecosystem: Mapped[str] = mapped_column(String(20), nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    package_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    degraded: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[CreatedAt]

    project: Mapped[Project] = relationship(back_populates="decisions")


class Sbom(Base):
    """SBOM CycloneDX generado para un proyecto y build."""

    __tablename__ = "sboms"

    id: Mapped[UUID_PK]
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    build_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sbom_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[CreatedAt]

    project: Mapped[Project] = relationship(back_populates="sboms")


class Build(Base):
    """Registro de un build ejecutado contra el firewall."""

    __tablename__ = "builds"

    id: Mapped[UUID_PK]
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    build_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    created_at: Mapped[CreatedAt]

    project: Mapped[Project] = relationship(back_populates="builds")


class PolicyException(Base):
    """Excepción de política por paquete o advisory, con vencimiento."""

    __tablename__ = "exceptions"

    id: Mapped[UUID_PK]
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_ecosystem: Mapped[str] = mapped_column(String(20), nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    package_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    advisory_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[CreatedAt]

    project: Mapped[Project] = relationship(back_populates="exceptions")


def create_all_tables(settings: Settings) -> None:
    """Crea todas las tablas en la base de datos (solo para pruebas/inicialización)."""

    sync_url = settings.database_url.replace("+asyncpg", "")
    engine = create_engine(sync_url, echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()
