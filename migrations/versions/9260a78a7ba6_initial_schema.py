"""Initial schema

Revision ID: 9260a78a7ba6
Revises:
Create Date: 2026-10-27 04:07:40.501701+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9260a78a7ba6"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Aplica la migración: crea todas las tablas."""
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_projects_name"), "projects", ["name"], unique=False)

    op.create_table(
        "tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tokens_project_id"), "tokens", ["project_id"], unique=False)

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_ecosystem", sa.String(length=20), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("package_version", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("degraded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_decisions_project_id"), "decisions", ["project_id"], unique=False)

    op.create_table(
        "sboms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_id", sa.String(length=255), nullable=True),
        sa.Column("sbom_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sboms_project_id"), "sboms", ["project_id"], unique=False)

    op.create_table(
        "builds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="completed", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_builds_project_id"), "builds", ["project_id"], unique=False)
    op.create_index(op.f("ix_builds_build_id"), "builds", ["build_id"], unique=False)

    op.create_table(
        "exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_ecosystem", sa.String(length=20), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("package_version", sa.String(length=100), nullable=True),
        sa.Column("advisory_id", sa.String(length=255), nullable=True),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_exceptions_project_id"), "exceptions", ["project_id"], unique=False)


def downgrade() -> None:
    """Revierte la migración: elimina todas las tablas."""
    op.drop_index(op.f("ix_exceptions_project_id"), table_name="exceptions")
    op.drop_table("exceptions")
    op.drop_index(op.f("ix_builds_build_id"), table_name="builds")
    op.drop_index(op.f("ix_builds_project_id"), table_name="builds")
    op.drop_table("builds")
    op.drop_index(op.f("ix_sboms_project_id"), table_name="sboms")
    op.drop_table("sboms")
    op.drop_index(op.f("ix_decisions_project_id"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_tokens_project_id"), table_name="tokens")
    op.drop_table("tokens")
    op.drop_index(op.f("ix_projects_name"), table_name="projects")
    op.drop_table("projects")
