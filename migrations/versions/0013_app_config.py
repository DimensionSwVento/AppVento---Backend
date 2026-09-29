"""app_config: store editable clave→valor (JSONB)

Store genérico para textos/parámetros que el admin ajusta sin tocar código.
Primer consumidor: `report_calc_text` (el "Resumen del cálculo" del informe,
editable desde admin — solo descriptivo, no recalcula el score).

Revision ID: 0013_app_config
Revises: 0012_infovariables_fields
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0013_app_config"
down_revision: Union[str, None] = "0012_infovariables_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("app_config")
