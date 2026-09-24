"""campos faltantes de InfoVariables: catalog_norma (fuente/url/condicion/variable_ref) + catalog_variable.comentarios

Auditoría de cobertura: que NADA de InfoVariables se ignore al ingerir por el drag
de metadatos.
- `catalog_norma`: la regulatoria de InfoVariables trae Fuente, URL, Condición de
  activación y, en Regulación-Social/Técnica/Ambiental, va ligada a una VARIABLE
  (código SOC/AMB) → `variable_ref`. Antes solo se guardaba estado/descripción.
- `catalog_variable.comentarios`: la columna "Comentarios/ideas sobre la forma de
  visualización" de las informativas se ignoraba.

Revision ID: 0012_infovariables_fields
Revises: 0011_economic_series
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_infovariables_fields"
down_revision: Union[str, None] = "0011_economic_series"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog_norma", sa.Column("fuente", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_norma", sa.Column("url_fuente", sa.String(), nullable=False, server_default=""))
    op.add_column("catalog_norma", sa.Column("condicion", sa.String(), nullable=False, server_default=""))
    # variable_ref: código de la variable a la que aplica la norma (Regulación-X);
    # "" para normas generales (REG-…). Índice para consultar por variable.
    op.add_column("catalog_norma", sa.Column("variable_ref", sa.String(), nullable=False, server_default=""))
    op.create_index("ix_catalog_norma_variable_ref", "catalog_norma", ["variable_ref"])

    op.add_column("catalog_variable", sa.Column("comentarios", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("catalog_variable", "comentarios")
    op.drop_index("ix_catalog_norma_variable_ref", table_name="catalog_norma")
    op.drop_column("catalog_norma", "variable_ref")
    op.drop_column("catalog_norma", "condicion")
    op.drop_column("catalog_norma", "url_fuente")
    op.drop_column("catalog_norma", "fuente")
