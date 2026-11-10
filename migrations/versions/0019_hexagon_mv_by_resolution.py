"""Vistas materializadas por resolución (mv_hexagon_res5, mv_hexagon_res6) — Fase C3

Objetivo: cuando el frontend pide una resolución agregada (`res` < 8, elegido
por el presupuesto de hexágonos en pantalla N — ver `resForHexBudget` en
`mapGeo.ts`), la consulta NO debe promediar miles de filas res8 EN CADA
REQUEST (eso es lo que hacía colapsar `/tiles/variables-bulk` a partir de ~10
peticiones concurrentes). En su lugar, el promedio se calcula UNA VEZ, al
refrescar la vista tras cada ingesta (Fase C4), y leerlo después es un simple
SELECT por índice sobre una tabla chica (mv_hexagon_res5 ≈ unos miles de filas;
mv_hexagon_res6 ≈ unas decenas de miles — ver benchmark-carga-h3 para el orden
de magnitud por resolución).

Cada vista agrega por la celda PADRE (`h3_res5`/`h3_res6`, Fase C1):
- `score_general`: promedio simple de los hijos.
- `is_excluded`: TRUE si ALGÚN hijo está excluido (BOOL_OR, no BOOL_AND) — a
  esta escala ya es una aproximación visual; marcar "excluido" solo cuando el
  100% de un área grande lo está ocultaría casi todas las exclusiones
  parciales. El consumo real de esta señal se decide en Fase C5.
- `lat`/`lng`: centroide aproximado (promedio), para la geometría de la fila
  agregada (no hay geometría H3 nativa sin `h3-pg`).
- `variables`: promedio NUMÉRICO por clave, re-empaquetado como JSONB. No
  existe una función nativa de Postgres para esto (el "jsonb_object_agg_avg"
  de la propuesta original no es una función real); se calcula con un CTE en
  dos pasos: (1) `jsonb_each_text` + filtro de valores numéricos + AVG
  agrupando por (padre, clave), (2) `jsonb_object_agg` para volver a armar el
  JSONB por padre. Valores no numéricos (strings categóricos) quedan fuera del
  promedio — igual que antes, estas vistas son para HEATMAPS de variables
  numéricas, no para el detalle exacto (que sigue viniendo de `hexagon` vía
  `/tiles/area`/`/tiles/detail`, sin pasar por aquí).

Costo de refresh: recorre `hexagon` completa (incluida `variables`) una vez
por ingesta — es CPU/IO de fondo, no de request; ver Fase C4 para el enganche
en los 4 caminos de ingesta y `REFRESH ... CONCURRENTLY`.

Índices únicos (`h3_index`) en cada vista: requisito de Postgres para poder
usar `REFRESH MATERIALIZED VIEW CONCURRENTLY` (cero downtime — la vista sigue
siendo consultable mientras se refresca).

Revision ID: 0019_hexagon_mv_by_resolution
Revises: 0018_hexagon_h3_parent_res
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019_hexagon_mv_by_resolution"
down_revision: Union[str, None] = "0018_hexagon_h3_parent_res"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mv_sql(res_col: str) -> str:
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
    op.execute(f"CREATE MATERIALIZED VIEW mv_hexagon_res5 AS{_mv_sql('h3_res5')}")
    op.execute("CREATE UNIQUE INDEX idx_mv_hexagon_res5_pk ON mv_hexagon_res5 (h3_index)")

    op.execute(f"CREATE MATERIALIZED VIEW mv_hexagon_res6 AS{_mv_sql('h3_res6')}")
    op.execute("CREATE UNIQUE INDEX idx_mv_hexagon_res6_pk ON mv_hexagon_res6 (h3_index)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hexagon_res6")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hexagon_res5")
