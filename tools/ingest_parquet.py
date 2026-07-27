"""
Ingesta DESTRUCTIVA del parquet → tabla `hexagon` (Postgres).

Borra todo el contenido previo de `hexagon` y lo reemplaza por las filas del
`.h3.parquet`. Pensada para cargar las MUESTRAS del modelo mientras llega el
dataset definitivo; luego, el mantenimiento se hace vía CRUD.

Uso:
    python -m tools.ingest_parquet                       # usa PARQUET_PATH del .env
    python -m tools.ingest_parquet ruta/al/otro.parquet  # ruta explícita

Requiere Postgres accesible (DATABASE_URL) con las tablas creadas (init_db).
"""
from __future__ import annotations
import sys

from shared.config import PARQUET_PATH
from infrastructure.db.session import SessionLocal, init_db
from application.services.tile_service import update_from_parquet


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else PARQUET_PATH
    print(f"Ingesta destructiva desde parquet: {path}")

    init_db()  # asegura que la tabla existe
    db = SessionLocal()
    try:
        n = update_from_parquet(db, path)
        print(f"✓ {n} hexágonos persistidos en `hexagon`.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
