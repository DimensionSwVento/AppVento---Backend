"""h3_index (PK) VARCHAR → BIGINT (tipo nativo del índice H3 de 64 bits)

Antes: la PK era VARCHAR con el token hex (p.ej. '8928308280fffff'). Ahora es
BIGINT: el entero nativo de 64 bits del índice H3. Índice más compacto (8 B vs
~16 B de varchar) y comparaciones/joins más baratos; prerequisito para `h3-pg`.

El token hex SIGUE siendo el contrato de la API y del front: la conversión vive
en el ORM (TypeDecorator `H3Index`) y en los caminos de SQL crudo (COPY, blue-green,
loadtest). Esta migración solo cambia el TIPO en la BD.

Afecta a dos tablas con `h3_index` como PK: `hexagon` (~1,6 M filas) y
`hexagon_variables_pon`.

USING: el token H3 es hexadecimal → ('x' || lpad(h3_index,16,'0'))::bit(64)::bigint
lo pasa a entero. El bit alto reservado del índice H3 es 0, así que entra en BIGINT
con signo sin desbordar (validado: valores < 2^63).

⚠ Despliegue: `ALTER COLUMN ... TYPE` REESCRIBE la tabla y RECONSTRUYE sus índices
(PK + `ix_hexagon_excluded`). Sobre ~1,6 M filas toma un lock exclusivo mientras
dura la reescritura → hacerlo en ventana de mantenimiento y validar antes en una
copia. Para cero-downtime real conviene el camino blue-green de la ingesta.

Revision ID: 0016_hexagon_h3_index_bigint
Revises: 0015_hexagon_excluded_index
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016_hexagon_h3_index_bigint"
down_revision: Union[str, None] = "0015_hexagon_excluded_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# hex (token H3) → bigint. Reutilizable para ambas tablas.
_HEX_TO_BIGINT = "('x' || lpad(h3_index, 16, '0'))::bit(64)::bigint"
# bigint → hex (token H3). `to_hex` da minúsculas sin ceros a la izquierda = el token.
_BIGINT_TO_HEX = "to_hex(h3_index)"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE hexagon "
        f"ALTER COLUMN h3_index TYPE BIGINT USING {_HEX_TO_BIGINT}"
    )
    op.execute(
        f"ALTER TABLE hexagon_variables_pon "
        f"ALTER COLUMN h3_index TYPE BIGINT USING {_HEX_TO_BIGINT}"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE hexagon "
        f"ALTER COLUMN h3_index TYPE VARCHAR USING {_BIGINT_TO_HEX}"
    )
    op.execute(
        f"ALTER TABLE hexagon_variables_pon "
        f"ALTER COLUMN h3_index TYPE VARCHAR USING {_BIGINT_TO_HEX}"
    )
