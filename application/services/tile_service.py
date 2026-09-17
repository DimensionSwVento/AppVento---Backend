"""
Capa de aplicación (STATELESS).

Orquesta cada consulta contra un `HexagonSource` (puerto): resuelve la geometría
a celdas/bbox (puro, h3), pide los datos a la fuente (Postgres por request) y
formatea la salida. NO carga nada a memoria al arrancar — la RAM ya no escala con
el tamaño del dataset.

La ingesta (`update_from_parquet`) vive aquí porque comparte el lector del parquet.
"""
from __future__ import annotations
from typing import Optional

from shared.config import PARQUET_PATH, HEXAGON_INGEST
from infrastructure.data.variable_catalog import dimension_of, get_variable_label

from domain.query_engine import geo, aggregator, variable_engine
from domain.query_engine.scoring import zoom_to_h3_res
from domain.query_engine.source import HexagonSource
from domain.models.hexagon import TileHex, HexDetail, AreaHex

# Resolución base del índice (los hexágonos del parquet están a res 8).
INDEX_RES = geo.INDEX_RES


# ── Categorías ────────────────────────────────────────────────────────────────

def extract_categories(variable_names: list[str]) -> list[str]:
    """Prefijos de categoría únicos (ej: TEC, SOE, REG)."""
    return sorted({
        v.split("_")[0] for v in variable_names
        if "_" in v and len(v.split("_")[0]) <= 3
    })


# ── Estado / metadatos ────────────────────────────────────────────────────────

def index_ready(source: HexagonSource) -> bool:
    return source.is_ready()


def warmup(source: HexagonSource) -> dict:
    """Bundle de metadatos (barato: count + `hexagon_meta`). Ya no carga datos."""
    names  = source.variable_names()
    ranges = source.variable_ranges()
    count  = source.count()
    return {
        "status":        "ready" if count else "empty",
        "count":         count,
        "variableNames": names,
        "categories":    extract_categories(names),
        "ranges":        ranges,
    }


def get_variables(source: HexagonSource) -> dict:
    names  = source.variable_names()
    ranges = source.variable_ranges()
    return {
        "variables": [
            {
                "name":      v,
                "label":     get_variable_label(v),
                "dimension": dimension_of(v),
                "range":     ranges.get(v),
            }
            for v in names
        ]
    }


def get_counts(source: HexagonSource, zoom: int, selected: list[str],
               var_name: Optional[str] = None) -> dict:
    return source.counts(selected, var_name)


# ── Capa de una variable en el mapa ───────────────────────────────────────────

def _resolve_variable_code(source: HexagonSource, code: str,
                           subtemplate: bool) -> Optional[str]:
    """Código pedido → variable real disponible.

    Si `code` existe en el índice, se usa tal cual. Si NO existe y `subtemplate`
    está activo (el front aún no conoce el código real de la variable, p.ej. las
    de viento), se elige una variable disponible de forma DETERMINISTA (hash del
    código pedido) para mostrar datos reales "de puente" — nunca al azar, así la
    misma variable pedida siempre cae en la misma real. Sin subtemplate → None.
    """
    names = source.variable_names()
    if code in names:
        return code
    if subtemplate and names:
        idx = sum(ord(c) for c in code) % len(names)
        return names[idx]
    return None


def get_variable_layer(source: HexagonSource, code: str,
                       subtemplate: bool = False) -> list[dict]:
    """Todos los hex (h3_index + valor normalizado 0–1) de una variable de mapa.

    Cobertura/heat de una variable (RAMSAR, viento…): escanea el índice por la
    variable y normaliza con su rango de `hexagon_meta`. Contrato del front:
    `[{h3_index, value}]`. Sin dato / variable inexistente → lista vacía.
    """
    var = _resolve_variable_code(source, code, subtemplate)
    if not var:
        return []
    rng = source.variable_range(var)
    span = (rng["max"] - rng["min"]) if rng else 0.0
    out: list[dict] = []
    for h3_index, raw in source.fetch_variable_cells(var):
        if raw is None:
            continue
        if rng and span > 0:
            v = (raw - rng["min"]) / span
            v = 0.0 if v < 0 else 1.0 if v > 1 else v
        else:
            v = 0.0
        out.append({"h3_index": h3_index, "value": round(v, 4)})
    return out


# ── Detalle ───────────────────────────────────────────────────────────────────

def get_detail(source: HexagonSource, h3_index: str) -> Optional[HexDetail]:
    hx = source.fetch_one(h3_index)
    if hx is None:
        return None
    return HexDetail(
        h3_index=hx.h3_index, score_general=hx.score_general, tipo_celda=hx.tipo_celda,
        lat=hx.lat, lng=hx.lng, variables=hx.variables,
    )


# ── Tile (mapa) ───────────────────────────────────────────────────────────────

def get_tile(source: HexagonSource, z: int, x: int, y: int,
             var: Optional[str] = None) -> list[TileHex]:
    west, south, east, north = geo.tile_to_bbox(z, x, y)
    rows = source.fetch_bbox(west, south, east, north)          # base (res 8) en el bbox

    target_res = zoom_to_h3_res(z)
    hexes = aggregator.aggregate(rows, target_res)              # agrupa a la res del zoom

    available = source.variable_names() if var else []
    var_range = source.variable_range(var) if var else None

    out: list[TileHex] = []
    for hx in hexes:
        value = None
        if var:
            value, _ = variable_engine.resolve_value(hx, var, available, var_range, "raw")
        out.append(TileHex(h=hx.h3_index, s=hx.score_general, t=hx.tipo_celda,
                           la=hx.lat, ln=hx.lng, v=value))
    return out


# ── Heatmap de área ───────────────────────────────────────────────────────────

def get_area_heatmap(source: HexagonSource, polygon: Optional[list], var: str,
                     resolution: int = INDEX_RES,
                     hexes: Optional[list[str]] = None) -> list[dict]:
    cells = _resolve_cells(polygon, hexes, resolution)
    if not cells:
        return []
    rows      = source.fetch_cells(cells)
    available = source.variable_names()
    var_range = source.variable_range(var)
    out = []
    for hx in rows:
        _, value_norm = variable_engine.resolve_value(hx, var, available, var_range, "normalized")
        out.append({"h": hx.h3_index, "value": value_norm})
    return out


# ── Informe de área (método principal) ────────────────────────────────────────

def get_area_report(source: HexagonSource, polygon: Optional[list] = None,
                    hexes: Optional[list[str]] = None,
                    resolution: int = INDEX_RES) -> list[AreaHex]:
    """
    Polígono (o lista de hexes) → solo los hexágonos de esa área, cada uno con
    score general, scores por dimensión (promedio normalizado por categoría) y
    los valores de sus variables. Es lo que consume el informe.
    """
    cells = _resolve_cells(polygon, hexes, resolution)
    if not cells:
        return []

    rows       = source.fetch_cells(cells)
    available  = source.variable_names()
    ranges     = source.variable_ranges()
    categories = extract_categories(available)
    vars_by_cat = {c: [v for v in available if v.startswith(c + "_")] for c in categories}

    out: list[AreaHex] = []
    for hx in rows:
        scores: dict[str, float] = {}
        for cat, cat_vars in vars_by_cat.items():
            norm_vals: list[float] = []
            for var in cat_vars:
                raw = variable_engine.get_variable_value(hx, var)
                rng = ranges.get(var)
                if raw is not None and rng:
                    norm_vals.append(variable_engine.normalize(raw, rng["min"], rng["max"]))
            if norm_vals:
                scores[cat] = round(sum(norm_vals) / len(norm_vals), 4)

        out.append(AreaHex(
            h3_index=hx.h3_index, lat=hx.lat, lng=hx.lng,
            score_general=hx.score_general, scores=scores,
            tipo_celda=hx.tipo_celda, variables=hx.variables,
        ))
    return out


def _resolve_cells(polygon: Optional[list], hexes: Optional[list[str]],
                   resolution: int) -> list[str]:
    """Geometría → lista de celdas a la resolución del índice (puro, sin BD).

    Los datos crudos por hex viven SIEMPRE a res 8 (INDEX_RES). Teselar el
    polígono a una resolución más gruesa (p.ej. res 7 del zoom lejano) genera
    hexágonos "padre" que NO existen en el índice base → `fetch_cells` no los
    encuentra y el dato real (siempre res 8) queda oculto. Por eso, tanto la
    rama de `hexes` (que expande de res 7 → hijos res 8) como la de `polygon`
    resuelven a INDEX_RES; el `resolution` entrante no reduce la granularidad
    del dato crudo. (Sub-tesela más fina que res 8 tampoco existe en el índice.)
    """
    if hexes:
        return list(geo.expand_to_index_res(hexes, INDEX_RES))
    if polygon:
        return list(geo.polygon_to_cells(polygon, INDEX_RES))
    return []


# ── Ingesta desde el parquet ──────────────────────────────────────────────────

def update_from_parquet(db, path: str | None = None, on_progress=None) -> int:
    """
    Ingesta DESTRUCTIVA del parquet → tablas `hexagon` + `hexagon_meta`.
    Streaming (no materializa el parquet). Método según `HEXAGON_INGEST`:
    "copy" (Postgres, rápido y atómico) u "orm" (fallback portable).
    """
    from infrastructure.data.parquet_reader import iter_parquet_rows
    from infrastructure.db import hexagon_repository as hexrepo

    rows = iter_parquet_rows(path or PARQUET_PATH)
    if HEXAGON_INGEST == "orm":
        return hexrepo.replace_all(db, rows, on_progress=on_progress)
    return hexrepo.copy_ingest(db, rows, on_progress=on_progress)
