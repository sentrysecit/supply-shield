"""Align projects.name with the model: one unique index

Revision ID: c2a91f6d3e57
Revises: 7b3f0c2e8d41
Create Date: 2026-11-10 23:30:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2a91f6d3e57"
down_revision: str | None = "7b3f0c2e8d41"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Reemplaza la restricción única y el índice redundante por un índice único.

    El modelo declara ``name`` con ``unique=True, index=True`` (un índice único), pero
    la migración inicial creó una restricción única más un índice no único. Con esa
    diferencia, ``alembic check`` falla y no sirve para detectar modelos y
    migraciones desalineados en la CI. La unicidad del nombre se mantiene.
    """
    op.drop_index(op.f("ix_projects_name"), table_name="projects")
    op.drop_constraint("projects_name_key", "projects", type_="unique")
    op.create_index(op.f("ix_projects_name"), "projects", ["name"], unique=True)


def downgrade() -> None:
    """Vuelve a la restricción única más el índice no único de la migración inicial."""
    op.drop_index(op.f("ix_projects_name"), table_name="projects")
    op.create_unique_constraint("projects_name_key", "projects", ["name"])
    op.create_index(op.f("ix_projects_name"), "projects", ["name"], unique=False)
