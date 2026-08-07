"""
Inspecciona el `.h3.parquet` y muestra su ESTRUCTURA REAL (la fuente de verdad
del esquema de hexágonos): columnas, tipos, nulos y separación metadatos/variables.

No deduce nada: reporta lo que trae el archivo. Úsalo cuando llegue la muestra o
el dataset definitivo para confirmar/ajustar la ingesta.

Uso:
    python -m tools.inspect_parquet                       # usa PARQUET_PATH del .env
    python -m tools.inspect_parquet ruta/al/otro.parquet
"""
from __future__ import annotations
import sys

import pandas as pd

from shared.config import PARQUET_PATH
from infrastructure.data.parquet_reader import _RESERVED


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else PARQUET_PATH
    print(f"Parquet: {path}\n")

    df = pd.read_parquet(path)
    n = len(df)
    print(f"Filas: {n}  ·  Columnas: {len(df.columns)}\n")

    reserved = [c for c in df.columns if c in _RESERVED]
    variables = [c for c in df.columns if c not in _RESERVED]

    print(f"— Metadatos (columnas fijas, del contrato _RESERVED): {reserved}")
    print(f"— Variables (van a `variables` JSONB): {len(variables)}\n")

    # Prefijos de dimensión (TEC, REG, SOE…) tal como los nombra el parquet
    dims: dict[str, int] = {}
    for v in variables:
        if "_" in v:
            dims[v.split("_")[0]] = dims.get(v.split("_")[0], 0) + 1
    print(f"— Dimensiones detectadas (prefijo → nº variables): {dict(sorted(dims.items()))}\n")

    print(f"{'columna':<24}{'dtype':<12}{'% nulos':>9}   ejemplo")
    print("-" * 70)
    for c in df.columns:
        nulls = df[c].isna().mean() * 100 if n else 0.0
        sample = df[c].dropna().iloc[0] if df[c].notna().any() else None
        tag = "meta" if c in _RESERVED else "var"
        print(f"{c:<24}{str(df[c].dtype):<12}{nulls:>8.1f}%   {sample!r:.30}  [{tag}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
