"""catalog_variable.metodo_calculo: "cómo se calcula" editable + ingestable

El texto de "cómo se calcula" estaba hardcodeado en el front (variableDetails.ts).
Ahora es un campo por variable, editable en admin e ingestable por metadatos
(perfil InfoVariables, keyword "como se calcula"/"metodo"/"formula"). El front lo
prefiere sobre la plantilla derivada.

Revision ID: 0014_variable_metodo_calculo
Revises: 0013_app_config
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_variable_metodo_calculo"
down_revision: Union[str, None] = "0013_app_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "catalog_variable",
        sa.Column("metodo_calculo", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("catalog_variable", "metodo_calculo")
