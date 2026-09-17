"""registro de fuentes de datos ingeridas (dónde se guarda cada doc + estado)

Persiste, por doc (master, variables, normas, escenarios, turbinas), DÓNDE está
guardado (ruta/URL) y el resultado de su última ingesta. Habilita la re-ingesta
de docs a demanda desde el panel de admin.

Revision ID: 0007_data_source
Revises: 0006_econ_turbine
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_data_source"
down_revision: Union[str, None] = "0006_econ_turbine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_source",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(), nullable=False, server_default="csv"),
        sa.Column("location", sa.String(), nullable=False, server_default=""),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("data_source")
