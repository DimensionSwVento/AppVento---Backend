"""
Ingesta DESTRUCTIVA del bundle del modelo por CONSOLA (como la master).

Reemplaza la tabla `hexagon` con los datos del bundle (varios .h3.parquet),
enrutando cada columna por su CLASE en el registro `parquet_column`. Solo corre si
NO hay columnas pendientes de conciliación (ver `python -m tools.ingest_docs`
para el catálogo, y el panel de admin para conciliar columnas del bundle).

⚠️ DESTRUCTIVA: borra `hexagon` y recarga. Igual que la ingesta de la master.

Uso:
  python -m tools.ingest_bundle <carpeta_del_bundle> [--emplazamiento onshore|offshore]
  python -m tools.ingest_bundle <carpeta_del_bundle> --plan     # solo muestra el plan (no ingiere)

Ejemplos:
  python -m tools.ingest_bundle /datos/onshore
  python -m tools.ingest_bundle /datos/onshore --plan
"""
from __future__ import annotations
import argparse
import sys

from infrastructure.db.session import SessionLocal
from application.services import bundle_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta destructiva del bundle del modelo.")
    parser.add_argument("root", help="carpeta del bundle (con los .h3.parquet)")
    parser.add_argument("--emplazamiento", default="onshore", choices=["onshore", "offshore"])
    parser.add_argument("--plan", action="store_true", help="solo mostrar el plan, no ingerir")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = bundle_service.plan(db, args.root)
        if "error" in plan:
            print(plan["error"], file=sys.stderr)
            return 2
        s = plan["summary"]
        # Tabla legible columna → CLASE detectada → destino (como en la HU de
        # conciliación). Se imprime SIEMPRE (antes de ingerir) para trazabilidad.
        print(f"\n{'COLUMNA':<22} {'CLASE':<22} {'ESTADO':<10} DESTINO")
        print("─" * 90)
        for c in plan["columns"]:
            print(f"{c['name']:<22} {str(c.get('kind') or '—'):<22} "
                  f"{c['estado']:<10} {c.get('destino') or '—'}")
        print("─" * 90)
        print(f"PLAN: {s['total']} columnas · {s['conocidas']} ok · "
              f"{s['propuestas']} propuestas · {s['pendientes']} pendientes\n")
        if args.plan:
            return 0
        if s["pendientes"]:
            print(f"⛔ Hay {s['pendientes']} columnas pendientes de conciliación. "
                  f"Resuélvelas (panel admin) antes de ingerir.", file=sys.stderr)
            return 3
        # Consola/prod = DESTRUCTIVA con backup + swap blue-green.
        result = bundle_service.apply(db, args.root, args.emplazamiento, mode="destructive")
        if "error" in result:
            print(result["error"], file=sys.stderr)
            return 3
        print(f"OK (destructiva/blue-green): {result['rows']} hexágonos ingeridos "
              f"({result['emplazamiento']}). Archivos: {', '.join(result['files'])}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
