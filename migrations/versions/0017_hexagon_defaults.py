"""defaults de `hexagon.scores` y `hexagon.emplazamiento`

`copy_ingest` (la ingesta rápida por COPY) NO incluye estas dos columnas en su
lista: las deja al DEFAULT del servidor. En una base creada desde cero el DEFAULT
no existía (solo el `default=` de SQLAlchemy, que es client-side), así que la
primera ingesta fallaba con `NotNullViolation` en `scores`/`emplazamiento`.

Este cambio es metadata-only (no reescribe la tabla) y hace que la ingesta
funcione en test/prod sin depender de ALTER manuales.

Revision ID: 0017_hexagon_defaults
Revises: 0016_hexagon_h3_index_bigint
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0017_hexagon_defaults"
down_revision: Union[str, None] = "0016_hexagon_h3_index_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "hexagon",
        "scores",
        server_default=sa.text("'{}'::jsonb"),
        existing_type=postgresql.JSONB(),
    )
    op.alter_column(
        "hexagon",
        "emplazamiento",
        server_default="onshore",
        existing_type=sa.String(),
    )


def downgrade() -> None:
    op.alter_column("hexagon", "scores", server_default=None,
                    existing_type=postgresql.JSONB())
    op.alter_column("hexagon", "emplazamiento", server_default=None,
                    existing_type=sa.String())
