"""versiones de la master + estampado de versión en el informe (US04-03)

`master_version`: cada actualización del dataset queda registrada con su estado
y momento de aplicación. `informe.master_version`: qué versión vio el informe.

Revision ID: 0004_master
Revises: 0003_catalog
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_master"
down_revision: Union[str, None] = "0003_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "master_version",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hex_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("var_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_master_version_applied_at", "master_version", ["applied_at"])
    op.create_index("ix_master_version_created_at", "master_version", ["created_at"])

    op.add_column("informe", sa.Column("master_version", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("informe", "master_version")
    op.drop_index("ix_master_version_created_at", table_name="master_version")
    op.drop_index("ix_master_version_applied_at", table_name="master_version")
    op.drop_table("master_version")
