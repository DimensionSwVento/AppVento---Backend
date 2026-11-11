"""
Ingesta del bundle por CONSOLA, POR CHUNKS (memory-safe).

A diferencia de `tools.ingest_bundle` (que mergea TODOS los parquets en pandas y
hace blue_green → pico de RAM = dataset completo, se cae por OOM en servers
chicos), este CLI reutiliza la MISMA maquinaria que la interfaz admin "subir
máster": `chunk_plan` → `apply_chunk` (row-group por row-group, UPSERT; el "merge"
lo hace Postgres vía ON CONFLICT) → `finalize` (anclas + versión + refresh de las
vistas por resolución). La memoria queda acotada a UN row-group por vez; se libera
entre lotes (`_trim_memory` dentro de `apply_chunk`).

Semántica: UPSERT incremental (fusiona columnas por `h3_index`, idempotente). NO
trunca la tabla — pensado para actualizar/poblar sobre el set existente. Si
necesitás un reemplazo LIMPIO total, usá `tools.ingest_bundle` (destructivo) en
una máquina con RAM holgada, o truncá antes a mano.

Requisito: NO debe haber columnas pendientes de conciliación (igual que la
interfaz). Si las hay, `apply_chunk` aborta — resolvelas antes (panel admin /
`python -m tools.ingest_docs`).

Uso:
    python -m tools.ingest_bundle_chunked <carpeta_del_bundle> [--emplazamiento onshore|offshore]
    python -m tools.ingest_bundle_chunked <carpeta_del_bundle> --plan   # solo lista los chunks

Ejemplos:
    python -m tools.ingest_bundle_chunked ./data/onshore_datos
    python -m tools.ingest_bundle_chunked ./data/onshore_datos --plan
"""
from __future__ import annotations
import argparse
import sys

from infrastructure.db.session import SessionLocal
from application.services import bundle_service


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta del bundle por chunks (memory-safe), por consola."
    )
    parser.add_argument("root", help="carpeta del bundle (con los .h3.parquet)")
    parser.add_argument("--emplazamiento", default="onshore",
                        choices=["onshore", "offshore"])
    parser.add_argument("--plan", action="store_true",
                        help="solo listar los chunks (no ingiere)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = bundle_service.chunk_plan(db, args.root)
        if "error" in plan:
            print(plan["error"], file=sys.stderr)
            return 2

        chunks = plan.get("chunks", [])
        total = plan.get("total_chunks", len(chunks))
        total_rows = plan.get("total_rows", 0)
        print(f"Plan por chunks: {total} lotes · {total_rows:,} filas · "
              f"emplazamiento={args.emplazamiento}", flush=True)

        if args.plan:
            for ch in chunks:
                print(f"  [{ch['index']:>4}] {ch['file']}  rg={ch['row_group']}  "
                      f"{ch.get('rows', 0):,} filas", flush=True)
            return 0

        if not chunks:
            print("⚠ No hay chunks para ingerir (¿carpeta vacía o sin parquets?).",
                  file=sys.stderr)
            return 3

        done_rows = 0
        for ch in chunks:
            res = bundle_service.apply_chunk(
                db, args.root, ch["file"], ch["row_group"],
                emplazamiento=args.emplazamiento,
            )
            if isinstance(res, dict) and "error" in res:
                print(f"\n✗ Falló el chunk [{ch['index']}] {ch['file']} "
                      f"rg={ch['row_group']}: {res['error']}", file=sys.stderr)
                return 4
            done_rows += ch.get("rows", 0)
            print(f"  ✓ [{ch['index'] + 1}/{total}] {ch['file']} rg={ch['row_group']} "
                  f"· acumulado {done_rows:,}/{total_rows:,} filas", flush=True)

        # Cierre: anclas de normalización + versión/fuente + REFRESH de las MVs
        # por resolución (una sola vez, no por chunk).
        fin = bundle_service.finalize(db, args.root, emplazamiento=args.emplazamiento)
        if isinstance(fin, dict) and "error" in fin:
            print(f"⚠ Chunks ingeridos, pero finalize falló: {fin['error']}",
                  file=sys.stderr)
            return 5

        print(f"✓ Ingesta por chunks completa: {done_rows:,} filas procesadas. "
              f"MVs por resolución refrescadas.", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
