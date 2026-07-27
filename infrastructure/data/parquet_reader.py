"""
Lectura del `.h3.parquet` → (list[HexagonData], nombres de variables, timestamp).
Procesar el parquet en el backend (no en el navegador) — ADR-011.
"""
from __future__ import annotations
import os
from datetime import datetime, timezone

import pandas as pd

from domain.models.hexagon import HexagonData

# Columnas que NO son variables (metadatos / geometría / score).
# El parquet real trae `h3_index` + variables codificadas (`TEC_001_sv001`, `REG_002`,
# `SOE_001`, `TEC_005`, …) con valores crudos y muchos `null`. Puede NO traer
# `score_general` / `tipo_celda` / `lat` / `lng`: en ese caso el score se calcula
# (calc_temp_viability), `tipo_celda` = "disponible" y lat/lng salen del h3_index.
_RESERVED = {"h3_index", "score_general", "tipo_celda", "lat", "lng"}


def read_parquet(path: str) -> tuple[list[HexagonData], list[str], str]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    df = pd.read_parquet(path)
    variable_names = [c for c in df.columns if c not in _RESERVED]

    hexagons: list[HexagonData] = []
    for rec in df.to_dict(orient="records"):
        variables = {
            k: (None if pd.isna(v) else v)
            for k, v in rec.items()
            if k in variable_names
        }
        score = rec.get("score_general")
        tipo = rec.get("tipo_celda")
        hexagons.append(
            HexagonData(
                h3_index=str(rec["h3_index"]),
                score_general=float(score) if score is not None and not pd.isna(score) else 0.0,
                # NaN es truthy: sin este guard un `tipo_celda` nulo se persistía como "nan".
                tipo_celda=str(tipo) if tipo is not None and not pd.isna(tipo) else "disponible",
                variables=variables,
            )
        )

    parsed_at = datetime.now(timezone.utc).isoformat()
    return hexagons, variable_names, parsed_at
