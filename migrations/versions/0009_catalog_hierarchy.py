"""catálogo jerárquico (dimension/criterio/subcriterio) + campos metadatos + column_mapping

Fase 1 del rediseño anidado (docs/spec/backend-arquitectura-anidada.md):
- Jerarquía de catálogo de PRIMERA CLASE: `catalog_dimension` → `catalog_criterio`
  → `catalog_subcriterio`, con definiciones por nivel (fiel al modelo anidado).
- `catalog_variable` gana los metadatos que trae InfoVariables(2) y NO estaban:
  `emplazamiento` (onshore/offshore/both), `licencia`, `url_fuente`, `icon_key`,
  `clase`, y el FK `subcriterio_id` a la jerarquía.
- `column_mapping`: registro de PERFILES de importación (encabezado CSV/XLSX →
  campo destino del catálogo). Lo usa el actualizador de metadatos (`metadata_ingest`)
  para clasificar columnas; lo desconocido queda pendiente de conciliación.

Revision ID: 0009_catalog_hierarchy
Revises: 0008_bundle
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_catalog_hierarchy"
down_revision: Union[str, None] = "0008_bundle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Jerarquía de catálogo (first-class) ──────────────────────────────────
    op.create_table(
        "catalog_dimension",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("definicion", sa.String(), nullable=False, server_default=""),
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("puntua", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "catalog_criterio",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dimension_id", sa.String(), nullable=False, server_default=""),
        sa.Column("code", sa.String(), nullable=False, server_default=""),
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("definicion", sa.String(), nullable=False, server_default=""),
        sa.Column("peso", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_criterio_dimension_id", "catalog_criterio", ["dimension_id"])
    op.create_table(
        "catalog_subcriterio",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("criterio_id", sa.String(), nullable=False, server_default=""),
        sa.Column("code", sa.String(), nullable=False, server_default=""),
        sa.Column("nombre", sa.String(), nullable=False, server_default=""),
        sa.Column("definicion", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_subcriterio_criterio_id", "catalog_subcriterio", ["criterio_id"])

    # ── catalog_variable: metadatos de InfoVariables(2) + FK a la jerarquía ──
    op.add_column("catalog_variable", sa.Column("subcriterio_id", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("emplazamiento", sa.String(), nullable=False, server_default="both"))
    op.add_column("catalog_variable", sa.Column("licencia", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("url_fuente", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_variable", sa.Column("icon_key", sa.String(), nullable=False, server_default=""))
    # clase: modelo|informativa|alerta|excluyente|campo (más fino que `tipo`).
    op.add_column("catalog_variable", sa.Column("clase", sa.String(), nullable=False, server_default="informativa"))
    op.create_index("ix_catalog_variable_subcriterio_id", "catalog_variable", ["subcriterio_id"])
    op.create_index("ix_catalog_variable_emplazamiento", "catalog_variable", ["emplazamiento"])

    # ── Perfiles de importación CSV/XLSX (actualizador de metadatos) ─────────
    op.create_table(
        "column_mapping",
        sa.Column("id", sa.String(), nullable=False),          # perfil:encabezado
        sa.Column("perfil", sa.String(), nullable=False, server_default=""),  # infovariables|economico|...
        sa.Column("encabezado", sa.String(), nullable=False, server_default=""),  # texto de la columna del doc
        sa.Column("destino", sa.String(), nullable=False, server_default=""),     # campo destino del catálogo
        sa.Column("transform", sa.String(), nullable=False, server_default=""),   # emplazamiento|bool_si_no|...
        sa.Column("estado", sa.String(), nullable=False, server_default="ok"),    # ok|pendiente|ignorada
        sa.Column("nota", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_column_mapping_perfil", "column_mapping", ["perfil"])


def downgrade() -> None:
    op.drop_index("ix_column_mapping_perfil", table_name="column_mapping")
    op.drop_table("column_mapping")
    op.drop_index("ix_catalog_variable_emplazamiento", table_name="catalog_variable")
    op.drop_index("ix_catalog_variable_subcriterio_id", table_name="catalog_variable")
    op.drop_column("catalog_variable", "clase")
    op.drop_column("catalog_variable", "icon_key")
    op.drop_column("catalog_variable", "url_fuente")
    op.drop_column("catalog_variable", "licencia")
    op.drop_column("catalog_variable", "emplazamiento")
    op.drop_column("catalog_variable", "subcriterio_id")
    op.drop_index("ix_catalog_subcriterio_criterio_id", table_name="catalog_subcriterio")
    op.drop_table("catalog_subcriterio")
    op.drop_index("ix_catalog_criterio_dimension_id", table_name="catalog_criterio")
    op.drop_table("catalog_criterio")
    op.drop_table("catalog_dimension")
