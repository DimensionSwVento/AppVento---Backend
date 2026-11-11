"""Escalera de resolución: h3_res2/3/4/7 + mv_hexagon_res2/3/4/7

Completa la escalera de vistas materializadas. Hasta ahora solo existían res5/6
(migraciones 0018/0019). Falta por ABAJO (res2/3/4, escalones de alejar con N chico
→ sin vista, el backend caía al stream_bbox del país entero) y por ARRIBA (res7).
La escalera queda res2 (toda Colombia) … res7; res8 NO necesita MV porque es el dato
NATIVO (la tabla `hexagon` ya es res8).

Agrega:
- Columnas padre `h3_res2`, `h3_res3`, `h3_res4`, `h3_res7` (BIGINT vía H3Index, nullable,
  indexadas). Como 0018, es SOLO esquema: las filas YA en la tabla quedan NULL
  hasta backfill (script `tools.backfill_h3_parents` con la librería `h3`) o la
  próxima ingesta (ya las puebla `_to_row`/copy/upsert). Sin backfill, las MVs de
  abajo quedan vacías (filtran `IS NOT NULL`) → correr el backfill y refrescar.
- Vistas `mv_hexagon_res2/3/4` con el MISMO agregado que 0019 (promedio de score,
  BOOL_OR de excluido, centroide, promedio numérico de `variables` por clave).

Revision ID: 0020_hexagon_res_ladder
Revises: 0019_hexagon_mv_by_resolution
Create Date: 2026-11-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from infrastructure.db.models import H3Index

revision: str = "0020_hexagon_res_ladder"
down_revision: Union[str, None] = "0019_hexagon_mv_by_resolution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_RES = (2, 3, 4, 7)  # res5/6 ya existen (0018/0019); res8 = tabla cruda


def _mv_sql(res_col: str) -> str:
    # Mismo cuerpo que la migración 0019 (duplicado intencional: las migraciones
    # deben quedar autocontenidas, sin importar código de la app).
    return f"""
        SELECT
          base.parent AS h3_index,
          base.score_general,
          base.is_excluded,
          base.lat,
          base.lng,
          COALESCE(agg_vars.variables, '{{}}'::jsonb) AS variables
        FROM (
          SELECT
            {res_col} AS parent,
            AVG(score_general)::real AS score_general,
            BOOL_OR(tipo_celda = 'excluida' OR score_general < 0) AS is_excluded,
            AVG(lat)::real AS lat,
            AVG(lng)::real AS lng
          FROM hexagon
          WHERE {res_col} IS NOT NULL
          GROUP BY {res_col}
        ) AS base
        LEFT JOIN (
          SELECT parent, jsonb_object_agg(key, avg_value) AS variables
          FROM (
            SELECT
              h.{res_col} AS parent,
              kv.key,
              AVG(kv.value::double precision) AS avg_value
            FROM hexagon h
            CROSS JOIN LATERAL jsonb_each_text(h.variables) AS kv(key, value)
            WHERE h.{res_col} IS NOT NULL
              AND kv.value ~ '^-?[0-9]+(\\.[0-9]+)?$'
            GROUP BY h.{res_col}, kv.key
          ) AS per_key
          GROUP BY parent
        ) AS agg_vars ON agg_vars.parent = base.parent
    """


def upgrade() -> None:
    for r in _NEW_RES:
        op.add_column("hexagon", sa.Column(f"h3_res{r}", H3Index(), nullable=True))
        op.create_index(f"ix_hexagon_h3_res{r}", "hexagon", [f"h3_res{r}"])
    for r in _NEW_RES:
        mv = f"mv_hexagon_res{r}"
        op.execute(f"CREATE MATERIALIZED VIEW {mv} AS{_mv_sql(f'h3_res{r}')}")
        op.execute(f"CREATE UNIQUE INDEX idx_{mv}_pk ON {mv} (h3_index)")


def downgrade() -> None:
    for r in reversed(_NEW_RES):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS mv_hexagon_res{r}")
    for r in reversed(_NEW_RES):
        op.drop_index(f"ix_hexagon_h3_res{r}", table_name="hexagon")
        op.drop_column("hexagon", f"h3_res{r}")
