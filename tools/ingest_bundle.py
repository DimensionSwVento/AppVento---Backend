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
    parser.add_argument("--pon-only", action="store_true",
                        help="solo ingerir variables_pon → hexagon_variables_pon (rápido, no toca hexagon)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.pon_only:
            n = bundle_service._ingest_pon(db, args.root, args.emplazamiento)
            if n:
                print(f"OK ponderadas: {n} hexágonos en hexagon_variables_pon ({args.emplazamiento}).")
            else:
                print("⚠ No se ingirió nada. ¿Falta 'variables_pon.h3.parquet' en la carpeta "
                      "o el emplazamiento no es onshore?", file=sys.stderr)
            return 0 if n else 3
        plan = bundle_service.plan(db, args.root)
        if "error" in plan:
            print(plan["error"], file=sys.stderr)
            return 2
        s = plan["summary"]
        # Archivos detectados (el plan deduplica columnas por nombre → la de
        # PONDERADAS `variables_pon.h3.parquet` no re-lista sus columnas, pero SÍ
        # se ingiere aparte a `hexagon_variables_pon`; se ve aquí que está).
        print("\nARCHIVOS DETECTADOS:")
        for f in plan.get("files", []):
            if "error" in f:
                print(f"  ⚠ {f['path']}: {f['error']}")
            else:
                pon = "  ← PONDERADAS" if "variables_pon" in f["path"] else ""
                print(f"  {f['path']:<48} {f.get('rows', 0):>12,} filas · "
                      f"{f.get('columns', 0)} cols{pon}")
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
        # Ponderadas → tabla propia (variables_pon.h3.parquet → hexagon_variables_pon).
        n_pon = result.get("rows_pon", 0)
        if n_pon:
            print(f"OK ponderadas: {n_pon} hexágonos en hexagon_variables_pon.")
        else:
            print("⚠ Sin ponderadas ingeridas (¿falta variables_pon.h3.parquet en la carpeta?).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
