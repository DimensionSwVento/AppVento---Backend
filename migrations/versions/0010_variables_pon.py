"""hexagon_variables_pon (ponderadas por hex, ONSHORE) — tabla propia

Fase 2 del rediseño anidado (docs/spec/backend-arquitectura-anidada.md).
Decisión winx (2026-09-22): las variables PONDERADAS van en su PROPIA tabla (no
JSONB en `hexagon`), porque el "descargar crudos" del informe las necesita — no se
desperdician datos. Se guardan aparte de las crudas (`hexagon.variables`).

Separación onshore/offshore por **TABLAS SEPARADAS** (decisión winx: "una tabla
nueva offshore vs onshore"), NO por partición: `hexagon`/`hexagon_variables_pon`
son ONSHORE; el offshore será un par de **tablas nuevas** (`hexagon_offshore`,
`hexagon_variables_pon_offshore`) cuando se entregue el bundle offshore.

Revision ID: 0010_variables_pon
Revises: 0009_catalog_hierarchy
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010_variables_pon"
down_revision: Union[str, None] = "0009_catalog_hierarchy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hexagon_variables_pon",
        sa.Column("h3_index", sa.String(), nullable=False),
        sa.Column("variables_pon", JSONB(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("h3_index"),
    )


def downgrade() -> None:
    op.drop_table("hexagon_variables_pon")
