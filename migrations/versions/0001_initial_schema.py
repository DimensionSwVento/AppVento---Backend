"""Esquema inicial: app_user, hexagon, informe

Refleja los modelos ORM (infrastructure/db/models.py). A diferencia del
`create_all` de dev, añade `server_default` para que las tablas sean usables
también por SQL directo (ingesta/mantenimiento fuera del ORM).

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── app_user: espejo local del usuario de Keycloak ────────────────────────
    op.create_table(
        "app_user",
        sa.Column("sub", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("roles", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("sub"),
    )

    # ── hexagon: espejo persistido del parquet (fuente de runtime del mapa) ────
    op.create_table(
        "hexagon",
        sa.Column("h3_index", sa.String(), nullable=False),
        sa.Column("score_general", sa.Float(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("tipo_celda", sa.String(), nullable=False,
                  server_default=sa.text("'disponible'")),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("variables", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("h3_index"),
    )

    # ── informe: informes guardados por usuario ───────────────────────────────
    op.create_table(
        "informe",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("hexes", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("variables", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("caracteristicas", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("scores_snapshot", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("area_km2", sa.Float(), nullable=False,
                  server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.sub"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_informe_user_id", "informe", ["user_id"])
    op.create_index("ix_informe_created_at", "informe", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_informe_created_at", table_name="informe")
    op.drop_index("ix_informe_user_id", table_name="informe")
    op.drop_table("informe")
    op.drop_table("hexagon")
    op.drop_table("app_user")
