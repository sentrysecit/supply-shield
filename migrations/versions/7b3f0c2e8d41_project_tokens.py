"""Project tokens managed by the admin API

Revision ID: 7b3f0c2e8d41
Revises: 4c1e7a9d2b30
Create Date: 2026-11-10 12:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b3f0c2e8d41"
down_revision: str | None = "4c1e7a9d2b30"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Agrega a los tokens su id público, nombre, autor y revocación auditada."""
    # Nadie escribía en esta tabla todavía (la autenticación lee config/projects.yaml).
    # Una fila antigua no tendría key_id ni autor y nunca podría usarse: se descarta.
    op.execute("DELETE FROM tokens")
    op.add_column("tokens", sa.Column("key_id", sa.String(length=32), nullable=False))
    op.add_column("tokens", sa.Column("name", sa.String(length=100), nullable=False))
    op.add_column("tokens", sa.Column("created_by", sa.String(length=255), nullable=False))
    op.add_column("tokens", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tokens", sa.Column("revoked_by", sa.String(length=255), nullable=True))
    op.add_column("tokens", sa.Column("revoke_reason", sa.Text(), nullable=True))
    op.create_index(op.f("ix_tokens_key_id"), "tokens", ["key_id"], unique=True)


def downgrade() -> None:
    """Vuelve al esquema anterior.

    Los tokens revocados se borran: el esquema anterior no sabe que lo están y,
    si la autenticación llegara a leer la tabla, volverían a valer.
    """
    op.execute("DELETE FROM tokens WHERE revoked_at IS NOT NULL")
    op.drop_index(op.f("ix_tokens_key_id"), table_name="tokens")
    op.drop_column("tokens", "revoke_reason")
    op.drop_column("tokens", "revoked_by")
    op.drop_column("tokens", "revoked_at")
    op.drop_column("tokens", "created_by")
    op.drop_column("tokens", "name")
    op.drop_column("tokens", "key_id")
