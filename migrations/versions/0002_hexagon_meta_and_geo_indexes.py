"""hexagon_meta (rangos por variable) + índices geo para tiles

Soporta el backend STATELESS: los rangos de normalización y el catálogo de
variables salen de `hexagon_meta` (precalculada en la ingesta), y las consultas
de tiles por bbox usan índices btree en `hexagon.lat`/`lng`.

Revision ID: 0002_hexagon_meta
Revises: 0001_initial
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_hexagon_meta"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hexagon_meta",
        sa.Column("variable", sa.String(), nullable=False),
        sa.Column("vmin", sa.Float(), nullable=False),
        sa.Column("vmax", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("variable"),
    )
    op.create_index("ix_hexagon_lat", "hexagon", ["lat"])
    op.create_index("ix_hexagon_lng", "hexagon", ["lng"])


def downgrade() -> None:
    op.drop_index("ix_hexagon_lng", table_name="hexagon")
    op.drop_index("ix_hexagon_lat", table_name="hexagon")
    op.drop_table("hexagon_meta")
