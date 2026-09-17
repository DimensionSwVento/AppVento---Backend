"""
Ingesta de documentos de datos por CONSOLA (equivalente CLI de la master).

La INTERFAZ hace el update normal (upsert: actualiza y añade). Este comando
permite además la ingesta DESTRUCTIVA (reemplazo total de la tabla con el
archivo), que es OPCIONAL y se corre a mano — como la ingesta destructiva de la
master.

Uso:
  python -m tools.ingest_docs <source> [--destructive]
  python -m tools.ingest_docs --list

  <source> ∈ {master, variables, normas, escenarios, turbines}
  --destructive   reemplazo total (por defecto: upsert, no borra)

Ejemplos:
  python -m tools.ingest_docs variables                 # upsert (igual que la UI)
  python -m tools.ingest_docs variables --destructive   # reemplazo total
"""
from __future__ import annotations
import argparse
import sys

from infrastructure.db.session import SessionLocal
from application.services import catalog_ingest


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta de docs de datos.")
    parser.add_argument("source", nargs="?", help="id del doc a ingerir")
    parser.add_argument("--destructive", action="store_true",
                        help="reemplazo total (borra la tabla y reinserta)")
    parser.add_argument("--list", action="store_true", help="lista las fuentes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list or not args.source:
            for s in catalog_ingest.list_sources(db):
                print(f"{s['id']:12} {s['status']:8} filas={s['rows']:<6} {s['location']}")
            return 0
        result = catalog_ingest.ingest(db, args.source, destructive=args.destructive)
        if result is None:
            print(f"Fuente '{args.source}' no registrada.", file=sys.stderr)
            return 2
        modo = "DESTRUCTIVA" if args.destructive else "upsert"
        print(f"Ingesta '{args.source}' ({modo}) OK → {result['rows']} filas. "
              f"Fuente: {result['location']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
