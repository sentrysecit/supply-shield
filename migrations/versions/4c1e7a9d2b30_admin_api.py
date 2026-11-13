"""Admin API: decision audit fields and revocable policy exceptions

Revision ID: 4c1e7a9d2b30
Revises: 9260a78a7ba6
Create Date: 2026-11-09 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4c1e7a9d2b30"
down_revision: str | None = "9260a78a7ba6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Agrega build y hallazgos a las decisiones y alinea las excepciones con la política."""
    # Decisiones: build y hallazgos completos para la auditoría (D4-5).
    op.add_column("decisions", sa.Column("build_id", sa.String(length=255), nullable=True))
    op.add_column(
        "decisions",
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_decisions_build_id"), "decisions", ["build_id"], unique=False)
    op.create_index(
        "ix_decisions_project_id_created_at",
        "decisions",
        ["project_id", "created_at"],
        unique=False,
    )

    # SBOMs: se consultan por build desde la API de administración.
    op.create_index(op.f("ix_sboms_build_id"), "sboms", ["build_id"], unique=False)

    # Excepciones: proyecto y paquete opcionales, regla, vencimiento por día (UTC,
    # inclusive, igual que en config/policy.yaml) y revocación auditada.
    op.alter_column("exceptions", "project_id", existing_type=postgresql.UUID(), nullable=True)
    op.alter_column(
        "exceptions", "package_ecosystem", existing_type=sa.String(length=20), nullable=True
    )
    op.alter_column(
        "exceptions", "package_name", existing_type=sa.String(length=255), nullable=True
    )
    op.add_column("exceptions", sa.Column("rule", sa.String(length=50), nullable=True))
    op.alter_column(
        "exceptions",
        "expires_at",
        new_column_name="expires_on",
        type_=sa.Date(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="(expires_at AT TIME ZONE 'UTC')::date",
    )
    op.add_column("exceptions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("exceptions", sa.Column("revoked_by", sa.String(length=255), nullable=True))
    op.add_column("exceptions", sa.Column("revoke_reason", sa.Text(), nullable=True))
    op.create_index(op.f("ix_exceptions_expires_on"), "exceptions", ["expires_on"], unique=False)
    op.create_check_constraint(
        "ck_exceptions_has_target",
        "exceptions",
        "package_name IS NOT NULL OR advisory_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_exceptions_package_complete",
        "exceptions",
        "(package_name IS NULL) = (package_ecosystem IS NULL)",
    )


def downgrade() -> None:
    """Revierte la migración.

    Las excepciones que no caben en el esquema anterior (sin proyecto o sin paquete)
    se borran, porque esas columnas vuelven a ser obligatorias. Las revocadas
    también: el esquema anterior no sabe marcarlas y volverían a aplicarse.
    """
    op.execute(
        "DELETE FROM exceptions "
        "WHERE project_id IS NULL OR package_name IS NULL OR revoked_at IS NOT NULL"
    )
    op.drop_constraint("ck_exceptions_package_complete", "exceptions", type_="check")
    op.drop_constraint("ck_exceptions_has_target", "exceptions", type_="check")
    op.drop_index(op.f("ix_exceptions_expires_on"), table_name="exceptions")
    op.drop_column("exceptions", "revoke_reason")
    op.drop_column("exceptions", "revoked_by")
    op.drop_column("exceptions", "revoked_at")
    op.alter_column(
        "exceptions",
        "expires_on",
        new_column_name="expires_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.Date(),
        existing_nullable=False,
        postgresql_using="(expires_on + interval '1 day' - interval '1 microsecond') "
        "AT TIME ZONE 'UTC'",
    )
    op.drop_column("exceptions", "rule")
    op.alter_column(
        "exceptions", "package_name", existing_type=sa.String(length=255), nullable=False
    )
    op.alter_column(
        "exceptions", "package_ecosystem", existing_type=sa.String(length=20), nullable=False
    )
    op.alter_column("exceptions", "project_id", existing_type=postgresql.UUID(), nullable=False)

    op.drop_index(op.f("ix_sboms_build_id"), table_name="sboms")

    op.drop_index("ix_decisions_project_id_created_at", table_name="decisions")
    op.drop_index(op.f("ix_decisions_build_id"), table_name="decisions")
    op.drop_column("decisions", "findings")
    op.drop_column("decisions", "build_id")
