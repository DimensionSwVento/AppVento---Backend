"""motor de ingesta por clases: registro parquet_column + hexagon.scores/emplazamiento

Habilita el consumo schema-agnóstico del bundle del modelo
(docs/spec/ingesta-clases-conciliacion.md):
- `parquet_column`: registro columna→clase (lo que un humano confirmó).
- `hexagon.scores` (JSONB): scores precalculados por criterio/subcriterio.
- `hexagon.emplazamiento`: onshore/offshore (bundle de origen).

Revision ID: 0008_bundle
Revises: 0007_data_source
Create Date: 2026-09-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008_bundle"
down_revision: Union[str, None] = "0007_data_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hexagon", sa.Column("scores", JSONB(), nullable=False, server_default="{}"))
    op.add_column("hexagon", sa.Column("emplazamiento", sa.String(), nullable=False, server_default="onshore"))
    op.create_index("ix_hexagon_emplazamiento", "hexagon", ["emplazamiento"])

    op.create_table(
        "parquet_column",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_code", sa.String(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(), nullable=False, server_default="variable_informativa"),
        sa.Column("target_ref", sa.String(), nullable=False, server_default=""),
        sa.Column("usos", sa.String(), nullable=False, server_default=""),
        sa.Column("tipo_dato", sa.String(), nullable=False, server_default=""),
        sa.Column("estado", sa.String(), nullable=False, server_default="ok"),
        sa.Column("nota", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_parquet_column_base_code", "parquet_column", ["base_code"])


def downgrade() -> None:
    op.drop_index("ix_parquet_column_base_code", table_name="parquet_column")
    op.drop_table("parquet_column")
    op.drop_index("ix_hexagon_emplazamiento", table_name="hexagon")
    op.drop_column("hexagon", "emplazamiento")
    op.drop_column("hexagon", "scores")
