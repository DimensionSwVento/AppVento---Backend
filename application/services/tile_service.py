"""
Capa de aplicación.
Responsabilidad: traducir requests HTTP a Query objects y formatear QueryResult
al shape que espera el router. No tiene lógica espacial ni de variables.
"""
from __future__ import annotations
import logging
from typing import Optional

import h3

from shared.config import PARQUET_PATH, HEXAGON_INGEST
from infrastructure.data.parquet_reader import read_parquet
from infrastructure.data.variable_catalog import dimension_of, get_variable_label
from infrastructure.spatial.tile_index import init_tile_index, is_index_ready
from infrastructure.spatial import tile_index as _idx

from domain.models.hexagon import TileHex, HexDetail, AreaHex
from domain.query_engine.models import Query, QueryResult, TileXYZ
from domain.query_engine import engine, spatial_resolver, variable_engine

# Resolución del índice (la del parquet). El área devuelve hexes a esta resolución.
INDEX_RES = 8

log = logging.getLogger("vento.tiles")


# ── Ingesta desde el parquet ──────────────────────────────────────────────────

def update_from_parquet(db, path: str | None = None, on_progress=None) -> int:
    """
    Ingesta DESTRUCTIVA del parquet → tabla `hexagon` (borra y recarga).
    Fuente única de la carga; la usan tanto el bootstrap del warmup como la CLI.
    El parquet es solo ENTRADA DE INGESTA (residual), nunca fuente de runtime.
    `path` se resuelve en tiempo de llamada (respeta la config vigente).

    Streaming: lee el parquet por lotes (no lo materializa entero → evita OOM).
    Método según `HEXAGON_INGEST`: "copy" (Postgres, rápido y atómico) u "orm"
    (fallback portable). `on_progress(total)` para logs de avance.
    """
    from infrastructure.data.parquet_reader import iter_parquet_rows
    from infrastructure.db import hexagon_repository as hexrepo

    rows = iter_parquet_rows(path or PARQUET_PATH)
    if HEXAGON_INGEST == "orm":
        return hexrepo.replace_all(db, rows, on_progress=on_progress)
    return hexrepo.copy_ingest(db, rows, on_progress=on_progress)


# ── Warmup ────────────────────────────────────────────────────────────────────

def _load_hexagons():
    """
    Fuente de runtime = Postgres (tabla `hexagon`). Si la tabla está VACÍA, se
    autopobla una vez ingiriendo desde el parquet (`update_from_parquet`) y se
    vuelve a leer de la BD. NO se sirve el parquet al índice directamente.
    """
    from infrastructure.db.session import SessionLocal
    from infrastructure.db import hexagon_repository as hexrepo

    db = SessionLocal()
    try:
        hexagons, variable_names, parsed_at = hexrepo.load_for_warmup(db)
        if not hexagons:
            log.warning("Tabla `hexagon` vacía — ingiriendo desde parquet (%s)…", PARQUET_PATH)
            n = update_from_parquet(db)
            log.info("Ingesta inicial: %s hexágonos persistidos en `hexagon`.", n)
            hexagons, variable_names, parsed_at = hexrepo.load_for_warmup(db)
        else:
            log.info("Warmup desde BD: %s hexágonos.", len(hexagons))
        return hexagons, variable_names, parsed_at
    finally:
        db.close()


def warmup() -> dict:
    hexagons, variable_names, parsed_at = _load_hexagons()
    init_tile_index(hexagons, variable_names)

    categories = extract_categories(variable_names)
    ranges     = {v: _idx.get_variable_range(v) for v in variable_names
                  if _idx.get_variable_range(v) is not None}

    return {
        "status":        "ready",
        "count":         len(hexagons),
        "variableNames": variable_names,
        "categories":    categories,
        "ranges":        ranges,
        "parsedAt":      parsed_at,
    }


def extract_categories(variable_names: list[str]) -> list[str]:
    """Extrae prefijos de categoría únicos (ej: TEC, SOE, REG)."""
    return sorted({v.split("_")[0] for v in variable_names if "_" in v and len(v.split("_")[0]) <= 3})


# ── Tile ──────────────────────────────────────────────────────────────────────

def get_tile(z: int, x: int, y: int, var_name: Optional[str] = None) -> list[TileHex]:
    query = Query(
        type="tile",
        geometry=TileXYZ(z=z, x=x, y=y),
        variable=var_name,
        mode="raw",
    )
    results = engine.execute(query)
    return [_to_tile_hex(r) for r in results]


def _to_tile_hex(r: QueryResult) -> TileHex:
    return TileHex(h=r.h3_index, s=r.score, t=r.tipo_celda,
                   la=r.lat, ln=r.lng, v=r.value)


# ── Area heatmap ──────────────────────────────────────────────────────────────

def get_area_heatmap(
    polygon: Optional[list],
    var: str,
    resolution: int = 8,
    hexes: Optional[list[str]] = None,
) -> list[dict]:
    """
    Dos modos:
    - hexes: lookup directo por h3_index (más eficiente, evita construir polígono en el cliente)
    - polygon: spatial resolver vía h3.polygon_to_cells
    """
    if hexes:
        return _heatmap_from_hexes(hexes, var)

    query = Query(
        type="area",
        geometry=polygon,
        variable=var,
        resolution=resolution,
        mode="normalized",
    )
    results = engine.execute(query)
    return [{"h": r.h3_index, "value": r.value_norm} for r in results]


def _heatmap_from_hexes(hexes: list[str], var: str) -> list[dict]:
    """Lookup directo en el índice para una lista de h3_indexes."""
    from domain.query_engine.variable_engine import resolve_value

    available = _idx.get_available_variables()
    var_range = _idx.get_variable_range(var)
    lookup    = _idx._hex_lookup or {}

    result = []
    for h in hexes:
        hx = lookup.get(h)
        if not hx:
            continue
        _, value_norm = resolve_value(hx, var, available, var_range, "normalized")
        result.append({"h": h, "value": value_norm})
    return result


# ── Detail ────────────────────────────────────────────────────────────────────

def get_detail(h3_index: str) -> Optional[HexDetail]:
    return _idx.get_hex_detail(h3_index)


# ── Variables ─────────────────────────────────────────────────────────────────

def get_variables() -> dict:
    vars_ = _idx.get_available_variables()
    return {
        "variables": [
            {
                "name":  v,
                "label": get_variable_label(v),   # código → nombre (catálogo)
                "dimension": dimension_of(v),      # TEC / AMB / SOE / MAC / REG
                "range": _idx.get_variable_range(v),
            }
            for v in vars_
        ]
    }


# ── Counts ────────────────────────────────────────────────────────────────────

def get_counts(zoom: int, selected: list[str], var_name: Optional[str] = None) -> dict:
    return _idx.get_global_counts(zoom, selected, var_name)


# ── Estado ────────────────────────────────────────────────────────────────────

def index_ready() -> bool:
    return is_index_ready()


# ── Informe de área (nuevo método) ────────────────────────────────────────────

def get_area_report(
    polygon: Optional[list] = None,
    hexes: Optional[list[str]] = None,
    resolution: int = INDEX_RES,
) -> list[AreaHex]:
    """
    Recibe el polígono del área (o la lista de hexes) y devuelve SOLO los
    hexágonos de esa área, cada uno con: score general, scores por dimensión
    (promedio normalizado de las variables de cada categoría) y los valores de
    sus variables (detalle). Es lo que consume el informe.
    """
    indexed = _resolve_area_hexes(polygon, hexes, resolution)
    if not indexed:
        return []

    available = _idx.get_available_variables()
    categories = extract_categories(available)
    vars_by_cat = {c: [v for v in available if v.startswith(c + "_")] for c in categories}

    out: list[AreaHex] = []
    for hx in indexed:
        scores: dict[str, float] = {}
        for cat, cat_vars in vars_by_cat.items():
            norm_vals: list[float] = []
            for var in cat_vars:
                raw = variable_engine.get_variable_value(hx, var)
                rng = _idx.get_variable_range(var)
                if raw is not None and rng:
                    norm_vals.append(variable_engine.normalize(raw, rng["min"], rng["max"]))
            if norm_vals:
                scores[cat] = round(sum(norm_vals) / len(norm_vals), 4)

        out.append(AreaHex(
            h3_index=hx.h3_index,
            lat=hx.lat,
            lng=hx.lng,
            score_general=hx.score_general,
            scores=scores,
            tipo_celda=hx.tipo_celda,
            variables=hx.variables,
        ))
    return out


def _resolve_area_hexes(polygon, hexes, resolution):
    """
    Devuelve los `_IndexedHex` del área. Dos modos:
    - `hexes`: lookup directo; un hex más grueso que el índice se expande a sus
      hijos a la resolución del índice (soporta la selección a res 7 del frontend).
    - `polygon`: `h3.polygon_to_cells` → lookup en el índice.
    """
    lookup = _idx._hex_lookup or {}

    if hexes:
        result = []
        for h in hexes:
            if h in lookup:
                result.append(lookup[h])
                continue
            try:
                if h3.get_resolution(h) < INDEX_RES:
                    for child in h3.cell_to_children(h, INDEX_RES):
                        if child in lookup:
                            result.append(lookup[child])
            except Exception:
                pass
        return result

    if polygon:
        query = Query(type="area", geometry=polygon, resolution=resolution, mode="raw")
        return spatial_resolver.resolve(
            query, get_at_resolution=_idx._get_at_resolution, hex_lookup=lookup
        )
    return []
