"""
Backfill de columnas padre H3 (h3_res2/3/4/5/6) en `hexagon` SIN re-ingestar.

Cuándo: tras una migración que AGREGA columnas `h3_res{N}` (ej. 0020 agrega
res2/3/4) cuando la tabla YA tiene datos. Esas columnas no se pueden calcular con
SQL puro (sin h3-pg), así que este script las llena en Python con la librería `h3`
(la misma del resto del backend): lee `h3_index` por lotes (keyset por la PK, sin
cargar todo en RAM), calcula el padre a cada resolución y hace UPDATE por lote.
Al final REFRESCA las MVs de esas resoluciones (que nacen vacías tras la migración).

Memoria acotada al lote (no al dataset) → no revienta en servers chicos (a
diferencia del merge de pandas de `ingest_bundle`).

Uso:
    python -m tools.backfill_h3_parents                # res 2,3,4 (las nuevas)
    python -m tools.backfill_h3_parents 2 3 4 5 6      # explícito
    python -m tools.backfill_h3_parents --batch 100000 2 3 4
"""
from __future__ import annotations
import sys

import h3
from sqlalchemy import text

from infrastructure.db.session import SessionLocal

DEFAULT_RES = (2, 3, 4, 7)  # las nuevas de la escalera (res5/6 ya pobladas)
DEFAULT_BATCH = 50_000


def main() -> int:
    args = [a for a in sys.argv[1:]]
    batch = DEFAULT_BATCH
    if "--batch" in args:
        i = args.index("--batch")
        batch = int(args[i + 1])
        del args[i : i + 2]
    res = tuple(int(a) for a in args) if args else DEFAULT_RES
    res = tuple(r for r in res if 1 <= r <= 15)
    if not res:
        print("sin resoluciones válidas", file=sys.stderr)
        return 2

    set_clause = ", ".join(f"h3_res{r} = :r{r}" for r in res)
    upd = text(f"UPDATE hexagon SET {set_clause} WHERE h3_index = :id")
    page = text(
        "SELECT h3_index FROM hexagon WHERE h3_index > :last "
        "ORDER BY h3_index LIMIT :lim"
    )

    db = SessionLocal()
    try:
        print(f"Backfill h3_res{res} (lote {batch:,})…", flush=True)
        last = -1
        total = 0
        while True:
            rows = db.execute(page.bindparams(last=last, lim=batch)).fetchall()
            if not rows:
                break
            params = []
            for (h3_int,) in rows:
                token = format(int(h3_int), "x")
                row = {"id": int(h3_int)}
                for r in res:
                    row[f"r{r}"] = int(h3.cell_to_parent(token, r), 16)
                params.append(row)
                last = int(h3_int)
            db.execute(upd, params)
            db.commit()
            total += len(rows)
            print(f"  … {total:,} filas", flush=True)

        # Las MVs de estas resoluciones nacen vacías tras la migración → refrescar.
        # REFRESH simple (no CONCURRENTLY): es un backfill puntual, la vista puede
        # estar unos segundos en recálculo.
        for r in res:
            mv = f"mv_hexagon_res{r}"
            try:
                db.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
                db.commit()
                print(f"  ✓ {mv} refrescada", flush=True)
            except Exception as exc:  # noqa: BLE001 — una MV faltante no aborta el resto
                db.rollback()
                print(f"  ⚠ no se pudo refrescar {mv}: {exc}", file=sys.stderr)

        print(f"✓ Backfill completo: {total:,} filas.", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
