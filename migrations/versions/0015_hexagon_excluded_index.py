"""índice PARCIAL para /tiles/excluded (zonas excluidas)

El endpoint /tiles/excluded consulta
    SELECT h3_index FROM hexagon WHERE tipo_celda = 'excluida' OR score_general < 0
que NO tenía índice → scan secuencial completo (~30 s sobre ~1,6 M filas), el peor
cuello del benchmark de carga. Este índice PARCIAL cubre exactamente ese predicado
y es diminuto (solo las filas excluidas) → la consulta pasa a ms.

Nota de despliegue: `op.create_index` toma un lock exclusivo breve (segundos) al
construirlo. Para cero-downtime sobre una tabla ya cargada, crearlo manualmente con
    CREATE INDEX CONCURRENTLY ix_hexagon_excluded ON hexagon (h3_index)
        WHERE tipo_celda = 'excluida' OR score_general < 0;
(fuera de transacción) y marcar esta migración como aplicada (`alembic stamp`).

Revision ID: 0015_hexagon_excluded_index
Revises: 0014_variable_metodo_calculo
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_hexagon_excluded_index"
down_revision: Union[str, None] = "0014_variable_metodo_calculo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = "tipo_celda = 'excluida' OR score_general < 0"


def upgrade() -> None:
    op.create_index(
        "ix_hexagon_excluded",
        "hexagon",
        ["h3_index"],
        unique=False,
        postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index("ix_hexagon_excluded", table_name="hexagon")
