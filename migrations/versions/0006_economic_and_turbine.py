"""económico real + turbinas en BD (dato real → BD, no hardcode)

3.2: `catalog_escenario` gana `mult` (modela la curva de la gráfica económica) e
     `impacto` (proyección ilustrativa por escenario).
4:   nueva tabla `catalog_turbine` (S/M/L) con potencia/área/espaciamiento — antes
     hardcodeado en projectData.ts (AEROGENERADORES).

Revision ID: 0006_econ_turbine
Revises: 0005_catvar_real
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_econ_turbine"
down_revision: Union[str, None] = "0005_catvar_real"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog_escenario", sa.Column("mult", sa.Float(), nullable=False, server_default="1.03"))
    op.add_column("catalog_escenario", sa.Column("impacto", sa.String(), nullable=False, server_default=""))

    op.create_table(
        "catalog_turbine",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False, server_default=""),
        sa.Column("potencia_min_mw", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("potencia_max_mw", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("area_por_aero_km2", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("espaciamiento_m", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("catalog_turbine")
    op.drop_column("catalog_escenario", "impacto")
    op.drop_column("catalog_escenario", "mult")
