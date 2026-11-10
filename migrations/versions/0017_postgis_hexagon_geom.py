"""Habilita PostGIS y agrega la columna geoespacial `geom` a `hexagon`

Prerrequisito de la Fase 2 de rendimiento (plan de optimización H3): reemplaza
el filtro por bbox basado en `lat`/`lng` (btree, rango) por un filtro espacial
real con PostGIS (`&&` sobre un índice GiST) — más preciso y más barato para
los escaneos por bbox que hoy tumban `/tiles/variables-bulk` a partir de ~10
peticiones concurrentes (ver benchmark-carga-h3).

Qué hace:
- Habilita la extensión `postgis` (si no existe).
- Agrega `hexagon.geom geometry(Point, 4326)` y hace backfill desde `lat`/`lng`
  para las filas ya existentes.
- Crea el índice espacial `idx_hexagon_geom` (GiST) sobre `geom`.
- Crea la función `hexagon_set_geom()` y un trigger `BEFORE INSERT OR UPDATE OF
  lat, lng` que mantiene `geom` sincronizado automáticamente — así los 4
  caminos de ingesta (`copy`, `orm`/`replace_all`, `upsert_chunk`,
  `blue_green_replace`) no necesitan saber nada de `geom` para las INSERT/COPY
  directas sobre la tabla `hexagon`.

Nota importante (blue-green): `blue_green_replace` crea `hexagon_new` con
`CREATE TABLE ... (LIKE hexagon INCLUDING ALL)`. El `LIKE` de PostgreSQL copia
índices/constraints/defaults, pero NUNCA copia triggers (ni con INCLUDING ALL).
Por eso `blue_green_replace` (infrastructure/db/hexagon_repository.py) fue
parcheado para re-crear el trigger explícitamente sobre `hexagon_new` justo
después de crearla — la función `hexagon_set_geom()` ya existe (creada aquí) y
se reutiliza. Una vez adjuntado a esa instancia de tabla, el trigger sobrevive
al RENAME del swap (rename sí preserva triggers).

`lat`/`lng` NO se eliminan: siguen siendo parte del contrato de datos de la
fila (se devuelven en las respuestas/HexRow); solo deja de usarse su índice
btree para el filtro por bbox en `hexagon_query_repository.fetch_bbox` /
`stream_bbox` (ver migración de código asociada, no de esquema).

Revision ID: 0017_postgis_hexagon_geom
Revises: 0016_hexagon_h3_index_bigint
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017_postgis_hexagon_geom"
down_revision: Union[str, None] = "0016_hexagon_h3_index_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Extensión geoespacial.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # 2) Columna + backfill desde lat/lng existentes.
    op.execute("ALTER TABLE hexagon ADD COLUMN geom geometry(Point, 4326)")
    op.execute(
        "UPDATE hexagon SET geom = ST_SetSRID(ST_MakePoint(lng, lat), 4326) "
        "WHERE lat IS NOT NULL AND lng IS NOT NULL"
    )

    # 3) Índice espacial (acelera `&&` / ST_Intersects por bbox).
    op.execute("CREATE INDEX idx_hexagon_geom ON hexagon USING GIST (geom)")

    # 4) Función + trigger: mantiene `geom` sincronizado con lat/lng en cada
    #    INSERT/UPDATE sobre la tabla `hexagon` (incluye COPY: los triggers de
    #    fila SÍ se disparan en COPY, no solo en INSERT regular).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION hexagon_set_geom() RETURNS trigger AS $$
        BEGIN
          IF NEW.lat IS NOT NULL AND NEW.lng IS NOT NULL THEN
            NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lng, NEW.lat), 4326);
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_hexagon_set_geom
        BEFORE INSERT OR UPDATE OF lat, lng ON hexagon
        FOR EACH ROW EXECUTE FUNCTION hexagon_set_geom()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_hexagon_set_geom ON hexagon")
    op.execute("DROP FUNCTION IF EXISTS hexagon_set_geom()")
    op.execute("DROP INDEX IF EXISTS idx_hexagon_geom")
    op.execute("ALTER TABLE hexagon DROP COLUMN IF EXISTS geom")
    # No se deshabilita la extensión `postgis`: podría estar en uso por otros objetos.
