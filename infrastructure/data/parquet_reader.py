"""
Lectura del `.h3.parquet` → (list[HexagonData], nombres de variables, timestamp).
Procesar el parquet en el backend (no en el navegador) — ADR-011.
"""
from __future__ import annotations
import os
from collections.abc import Iterator
from datetime import datetime, timezone

import h3
import pandas as pd
import pyarrow.parquet as pq

from domain.models.hexagon import HexagonData
from domain.query_engine.scoring import effective_score, is_in_colombia

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


def iter_parquet_rows(path: str, batch_size: int = 20000) -> Iterator[dict]:
    """
    Itera el parquet POR LOTES (memoria acotada, no carga todo a la vez) →
    un dict por fila: {h3_index, score_general, tipo_celda, variables}.

    Para la ingesta destructiva a la BD: evita el pico de RAM de `read_parquet`
    (que materializa el dataset completo varias veces). pyarrow devuelve `None`
    para los nulos, así que aquí no hay `NaN` que sanear.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    pf = pq.ParquetFile(path)
    all_cols = [f.name for f in pf.schema_arrow]
    variable_names = [c for c in all_cols if c not in _RESERVED]

    for batch in pf.iter_batches(batch_size=batch_size):
        for rec in batch.to_pylist():  # solo materializa un lote
            h3_index = str(rec["h3_index"])
            lat, lng = h3.cell_to_latlng(h3_index)
            if not is_in_colombia(lat, lng):
                continue  # el filtro geográfico se aplica UNA vez, en la ingesta
            variables = {k: rec.get(k) for k in variable_names}
            score = rec.get("score_general")
            tipo = rec.get("tipo_celda")
            yield {
                "h3_index": h3_index,
                # score efectivo persistido (placeholder si el parquet no trae score)
                "score_general": effective_score(float(score) if score is not None else 0.0, variables),
                "tipo_celda": str(tipo) if tipo is not None else "disponible",
                "lat": lat,
                "lng": lng,
                "variables": variables,
            }
