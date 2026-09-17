"""
Inspector de estructura + CABECERA de un bundle de .parquet (SIN exponer datos).

Genera un .txt que, igual que `modelo.txt`, lista el ÁRBOL de carpetas/archivos y,
para cada `.parquet`, guarda SOLO su ESQUEMA (nombres y tipos de columna + nº de
filas). NO lee ni escribe ningún valor de dato → seguro para información
confidencial: solo toca el footer de metadatos de cada parquet.

Requiere pyarrow (motor de parquet):  pip install pyarrow

Uso:
  python tools/inspect_parquets.py <ruta_del_bundle> [salida.txt]

Ejemplos:
  python tools/inspect_parquets.py ./modelo
  python tools/inspect_parquets.py /ruta/confidencial/modelo estructura_modelo.txt
"""
from __future__ import annotations
import os
import sys

try:
    import pyarrow.parquet as pq
except ImportError:
    print("Falta pyarrow. Instala con:  pip install pyarrow", file=sys.stderr)
    raise SystemExit(1)


def _tree(root: str) -> list[str]:
    """Árbol de carpetas/archivos (estilo `ls -R`, relativo a root)."""
    lines: list[str] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        indent = "  " * depth
        name = os.path.basename(root) if rel == "." else os.path.basename(dirpath)
        lines.append(f"{indent}{name}/")
        for f in filenames:
            lines.append(f"{indent}  {f}")
    return lines


def _schema_of(path: str) -> list[str]:
    """Esquema (cabecera) de un .parquet: columnas+tipos y nº de filas. SIN datos."""
    out: list[str] = []
    try:
        # read_schema lee SOLO el footer (metadatos) — no carga ninguna fila.
        schema = pq.read_schema(path)
        n_rows = pq.ParquetFile(path).metadata.num_rows
        out.append(f"  filas={n_rows}  columnas={len(schema.names)}")
        for field in schema:
            out.append(f"    - {field.name}: {field.type}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"  ⚠️ no se pudo leer el esquema: {exc}")
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "estructura_parquets.txt"
    if not os.path.isdir(root):
        print(f"No es una carpeta: {root}", file=sys.stderr)
        return 2

    lines: list[str] = []
    lines.append("# Estructura + cabeceras del bundle de parquets")
    lines.append("# Generado por tools/inspect_parquets.py — SOLO esquema, sin datos.")
    lines.append("")
    lines.append("## 1. ESTRUCTURA (árbol)")
    lines += _tree(root)
    lines.append("")
    lines.append("## 2. ESQUEMA POR .parquet (cabecera: columnas + tipos + nº filas)")

    n_parquets = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for f in sorted(filenames):
            if f.endswith(".parquet"):
                n_parquets += 1
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, os.path.abspath(root))
                lines.append("")
                lines.append(f"### {rel}")
                lines += _schema_of(full)

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"OK: {n_parquets} .parquet inspeccionados → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
