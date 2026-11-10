"""Columnas padre H3 (h3_res5, h3_res6) en `hexagon` — Fase C1 (rendimiento)

Prerrequisito de las vistas materializadas por resolución (Fase C3,
`mv_hexagon_res5`/`mv_hexagon_res6`): necesitan agrupar `hexagon` por la celda
PADRE en esa resolución (`GROUP BY h3_res5` / `GROUP BY h3_res6`). Sin estas
columnas, cada refresh de la vista tendría que recalcular el padre de 1,6 M
celdas al vuelo.

Por qué NO se calcula con una función SQL nativa de H3: eso requeriría la
extensión `h3-pg`, que no está confirmada como instalada/instalable en todos
los entornos (a diferencia de PostGIS, mucho más estándar). En su lugar, el
padre se calcula en PYTHON con la librería `h3` (ya una dependencia del
backend, usada en `domain/query_engine/aggregator.py` y `tile_service.py`) en
el momento de la ingesta — ver `_h3_parent_bigint` en
`infrastructure/db/hexagon_repository.py`, enganchado en los 4 caminos de
ingesta (`copy_ingest`, `_to_row` → `replace_all`/`upsert_chunk`/`upsert_all`/
`blue_green_replace`).

Importante — esta migración es SOLO esquema (DDL), no backfill: no hay forma
de calcular el padre H3 de una celda existente con SQL puro (sin h3-pg). Las
filas YA EN LA TABLA quedan con `h3_res5`/`h3_res6` NULL hasta la PRÓXIMA
ingesta completa (la máster se recarga por diseño — ver `blue_green_replace`);
no se pintan en `mv_hexagon_res5`/`res6` hasta entonces. Si se necesita
backfill inmediato sin reingestar, correr un script puntual con la librería
`h3` (lectura por lotes + `UPDATE ... WHERE h3_index = ANY(...)`).

Revision ID: 0018_hexagon_h3_parent_res
Revises: 0017_postgis_hexagon_geom
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from infrastructure.db.models import H3Index

revision: str = "0018_hexagon_h3_parent_res"
down_revision: Union[str, None] = "0017_postgis_hexagon_geom"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hexagon", sa.Column("h3_res5", H3Index(), nullable=True))
    op.add_column("hexagon", sa.Column("h3_res6", H3Index(), nullable=True))
    op.create_index("ix_hexagon_h3_res5", "hexagon", ["h3_res5"])
    op.create_index("ix_hexagon_h3_res6", "hexagon", ["h3_res6"])


def downgrade() -> None:
    op.drop_index("ix_hexagon_h3_res6", table_name="hexagon")
    op.drop_index("ix_hexagon_h3_res5", table_name="hexagon")
    op.drop_column("hexagon", "h3_res6")
    op.drop_column("hexagon", "h3_res5")
