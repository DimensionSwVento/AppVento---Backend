"""catálogo de administración: variables, normas y escenarios (US08-01/02/03)

Tablas que persisten el catálogo editable por el admin desde el frontend
(`/api/admin/catalog/*`). Los defaults van como server_default para que la tabla
sea autosuficiente ante inserts crudos, además del default de la ORM.

Revision ID: 0003_catalog
Revises: 0002_hexagon_meta
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_catalog"
down_revision: Union[str, None] = "0002_hexagon_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_variable",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=False),
        sa.Column("dimension_label", sa.String(), nullable=False, server_default=""),
        sa.Column("tipo", sa.String(), nullable=False, server_default="informativa"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("en_mapa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("en_menu", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("excluyente", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fuente", sa.String(), nullable=False, server_default="Por definir"),
        sa.Column("unidad", sa.String(), nullable=False, server_default=""),
        sa.Column("rango", sa.String(), nullable=False, server_default=""),
        sa.Column("descripcion", sa.String(), nullable=False, server_default=""),
        sa.Column("dataset_version", sa.String(), nullable=False, server_default="v1.0"),
        sa.Column("dataset_updated_at", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "catalog_norma",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("descripcion", sa.String(), nullable=False, server_default=""),
        sa.Column("estado", sa.String(), nullable=False, server_default="verificacion"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "catalog_escenario",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("horizonte", sa.Integer(), nullable=False, server_default="10"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("catalog_escenario")
    op.drop_table("catalog_norma")
    op.drop_table("catalog_variable")
