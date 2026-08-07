#!/bin/sh
# Arranque del contenedor:
#   1) Aplica migraciones (crea/actualiza las 3 tablas).
#   2) Si RUN_INGEST=true, ingiere el parquet a la tabla `hexagon` (destructivo).
#      Si no, el warmup autopobla `hexagon` desde el parquet cuando está vacía.
#   3) Ejecuta el comando del contenedor (gunicorn).
set -e

echo "[entrypoint] alembic upgrade head…"
alembic upgrade head

if [ "$RUN_INGEST" = "true" ]; then
    echo "[entrypoint] Ingesta destructiva del parquet → hexagon…"
    python -m tools.ingest_parquet
fi

echo "[entrypoint] arrancando: $*"
exec "$@"
