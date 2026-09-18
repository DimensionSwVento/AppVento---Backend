"""
Capa de aplicación (STATELESS).

Orquesta cada consulta contra un `HexagonSource` (puerto): resuelve la geometría
a celdas/bbox (puro, h3), pide los datos a la fuente (Postgres por request) y
formatea la salida. NO carga nada a memoria al arrancar — la RAM ya no escala con
el tamaño del dataset.

La ingesta (`update_from_parquet`) vive aquí porque comparte el lector del parquet.
"""
from __future__ import annotations
import hashlib
import logging
from typing import Optional

from shared.config import PARQUET_PATH, HEXAGON_INGEST, OFFSHORE_VAR
from infrastructure.data.variable_catalog import dimension_of, get_variable_label, base_code
from infrastructure.data import model_weights
from domain.query_engine import geo, aggregator, variable_engine
from domain.query_engine.scoring import zoom_to_h3_res
from domain.query_engine.source import HexagonSource
from domain.models.hexagon import TileHex, HexDetail, AreaHex

log = logging.getLogger("vento")

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
        # Mar/tierra por hex si la master trae la columna configurada (nombre TBD).
        off = _offshore_flag(hx.variables) if OFFSHORE_VAR else None
        out.append(TileHex(h=hx.h3_index, s=hx.score_general, t=hx.tipo_celda,
                           la=hx.lat, ln=hx.lng, v=value, off=off))
    return out


def _offshore_flag(variables: dict) -> Optional[int]:
    """1/0 mar/tierra desde la columna `OFFSHORE_VAR` de la master (o None)."""
    raw = variables.get(OFFSHORE_VAR)
    if raw is None:
        return None
    try:
        return 1 if float(raw) >= 0.5 else 0
    except (TypeError, ValueError):
        return None


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


# BBox aproximado de Colombia (continental + insular). Fallback si el front no
# manda uno — devuelve la variable para todo el país (puede ser pesado; el front
# debería mandar el bbox del viewport para acotar).
_COLOMBIA_BBOX = {"west": -82.5, "south": -4.5, "east": -66.0, "north": 13.6}


def _resolve_code(available: list[str], code: str, subtemplate: bool) -> tuple[Optional[str], bool]:
    """
    Resuelve el `code` pedido contra las variables disponibles en la tabla.
    - Casa directo o por código base (`TEC-004-T`→`TEC_004`).
    - Si NO casa y `subtemplate` está activo, elige uno AL AZAR DETERMINISTA
      (por el propio `code`) de los disponibles → así, mientras no se sepa qué
      código es viento/densidad, el mapa muestra datos reales de ALGUNA variable
      (cada `code` mapea siempre al mismo, y códigos distintos a distintos).
    Devuelve `(código_efectivo | None, es_subtemplate)`.
    """
    if not available:
        return None, False
    target = base_code(code.replace("-", "_"))
    for a in available:
        if a == code or base_code(a) == target:
            return a, False
    if not subtemplate:
        return None, False
    ordered = sorted(available)
    idx = int(hashlib.md5(code.encode("utf-8")).hexdigest(), 16) % len(ordered)
    return ordered[idx], True


def get_variable_layer(source: HexagonSource, code: str,
                       bbox: Optional[dict] = None,
                       threshold: float = 0.0,
                       subtemplate: bool = False) -> list[dict]:
    """
    Capa de una variable a nivel país/bbox en res8 (US04-03 · `map-res8-contract`):
    `[{h3_index, value}]` con el valor normalizado 0–1 donde la variable está
    presente, acotado por bbox y umbral. Sin bbox usa Colombia entera.

    `subtemplate`: si el `code` no se reconoce, usa una variable disponible al
    azar (determinista) — modo puente hasta conocer el mapeo real de códigos.
    """
    b = bbox or _COLOMBIA_BBOX
    rows = source.fetch_bbox(b["west"], b["south"], b["east"], b["north"])
    available = source.variable_names()
    eff_code, is_sub = _resolve_code(available, code, subtemplate)
    if eff_code is None:
        return []  # código no reconocido y sin subtemplate → nada que pintar
    if is_sub:
        log.info("tiles/variable: '%s' no reconocido → subtemplate '%s'.", code, eff_code)
    var_range = source.variable_range(eff_code)
    out: list[dict] = []
    for hx in rows:
        _, value_norm = variable_engine.resolve_value(
            hx, eff_code, available, var_range, "normalized"
        )
        if value_norm is None or value_norm < threshold:
            continue  # variable ausente en el hex o bajo el umbral
        out.append({"h3_index": hx.h3_index, "value": value_norm})
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
        gen_num = 0.0  # Σ(peso_dim · score_dim)
        gen_den = 0.0  # Σ peso_dim
        for cat, cat_vars in vars_by_cat.items():
            # score_dim = Σ(peso_var · valor_norm) / Σ peso_var  (WLC/AHP).
            num = 0.0
            den = 0.0
            for var in cat_vars:
                raw = variable_engine.get_variable_value(hx, var)
                rng = ranges.get(var)
                if raw is not None and rng:
                    norm = variable_engine.normalize(raw, rng["min"], rng["max"])
                    w = model_weights.variable_weight(var)
                    num += w * norm
                    den += w
            if den > 0:
                score_cat = round(num / den, 4)
                scores[cat] = score_cat
                wd = model_weights.dimension_weight(cat)
                gen_num += wd * score_cat
                gen_den += wd

        # índice general = Σ(peso_dim · score_dim) / Σ peso_dim. Si la master ya
        # trae un score_general (> 0), se respeta; si no, se usa el calculado.
        computed_general = round(gen_num / gen_den, 4) if gen_den > 0 else 0.0
        score_general = (
            hx.score_general if hx.score_general and hx.score_general > 0
            else computed_general
        )

        out.append(AreaHex(
            h3_index=hx.h3_index, lat=hx.lat, lng=hx.lng,
            score_general=score_general, scores=scores,
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
