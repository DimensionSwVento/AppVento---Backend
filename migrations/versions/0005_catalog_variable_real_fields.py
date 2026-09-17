"""campos reales por-variable en catalog_variable (dato real → BD, no hardcode)

Mueve a BD lo que estaba hardcodeado en el front (variablesCatalog.ts /
variableWeights.ts / mapLayerData.ts): identidad master, criterio/subcriterio,
emplazamiento, polaridad/peso del WLC y cómo se pinta en el mapa. Persisten y se
editan desde el panel de admin (a diferencia de la master, que se reingesta).

Revision ID: 0005_catvar_real
Revises: 0004_master
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_catvar_real"
down_revision: Union[str, None] = "0004_master"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog_variable", sa.Column("master_id", sa.String(), nullable=False, server_default="NA"))
    op.add_column("catalog_variable", sa.Column("criterio", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("subcriterio", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("aplica_en", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("no_calculo", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("catalog_variable", sa.Column("polaridad", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("catalog_variable", sa.Column("peso", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("catalog_variable", sa.Column("map_kind", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("map_threshold", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    for col in (
        "map_threshold", "map_kind", "peso", "polaridad", "no_calculo",
        "aplica_en", "subcriterio", "criterio", "master_id",
    ):
        op.drop_column("catalog_variable", col)
