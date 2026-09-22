"""economic_series (long) + economic_panel — dimensión económica macro (Fase 3)

Fase 3 del rediseño anidado (docs/spec/backend-arquitectura-anidada.md §6.3).
La económica es un modelo macro CGE (GAMS) por escenario de MW de generación,
INFORMATIVA (no puntúa, no toca `hexagon`). Sus salidas (`panel_por_variable.xlsx`,
26 hojas) vienen en formato TIDY: `escenario_mw | cat1..catN | anio | valor`.

- `economic_series`: tabla LONG genérica (una fila por punto) que cubre las 26
  hojas heterogéneas con `panel` (hoja) + `cat1..cat3` + escenario_mw + anio + valor.
- `economic_panel`: catálogo de hojas (de la hoja RESUMEN): id, nombre, descripción.

Revision ID: 0011_economic_series
Revises: 0010_variables_pon
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_economic_series"
down_revision: Union[str, None] = "0010_variables_pon"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "economic_panel",
        sa.Column("id", sa.String(), nullable=False),        # nombre de hoja (PIB, EMPLEO…)
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("descripcion", sa.String(), nullable=False, server_default=""),
        sa.Column("curado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "economic_series",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("panel", sa.String(), nullable=False),         # hoja de origen
        sa.Column("escenario_mw", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cat1", sa.String(), nullable=False, server_default=""),
        sa.Column("cat2", sa.String(), nullable=False, server_default=""),
        sa.Column("cat3", sa.String(), nullable=False, server_default=""),
        sa.Column("anio", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valor", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_economic_series_panel", "economic_series", ["panel"])
    op.create_index(
        "ix_economic_series_lookup", "economic_series", ["panel", "escenario_mw"]
    )


def downgrade() -> None:
    op.drop_index("ix_economic_series_lookup", table_name="economic_series")
    op.drop_index("ix_economic_series_panel", table_name="economic_series")
    op.drop_table("economic_series")
    op.drop_table("economic_panel")
