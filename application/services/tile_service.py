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

import h3

from shared.config import PARQUET_PATH, HEXAGON_INGEST, OFFSHORE_VAR
from infrastructure.data.variable_catalog import dimension_of, get_variable_label, base_code
from infrastructure.data import model_weights
from domain.query_engine import geo, aggregator, variable_engine
from domain.query_engine.scoring import (
    zoom_to_h3_res, normalize_model_score, is_excluded_by_score,
)
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


# La dimensión SOCIAL del modelo = entorno_social (SOC) + economía-SOE (§2 del
# modelo anidado): sus ponderadas puntúan JUNTAS → se canoniza el prefijo SOE a
# SOC para promediarlas en un único score Social (antes salían separadas y el
# front tomaba solo una → Social incompleta). ECO (macro) y REG son informativas.
_CAT_CANON: dict[str, str] = {"SOE": "SOC"}


def _canon_cat(prefix: str) -> str:
    return _CAT_CANON.get(prefix, prefix)


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


def get_variables(source: HexagonSource, labels: Optional[dict] = None) -> dict:
    """`labels` = mapa base_code→{label,dimension,dimensionLabel} de `catalog_variable`
    (BD). Si una variable está ahí, se etiqueta con el dato REAL ingerido; si no
    (o sin ingesta), cae al nombre derivado del código (stub). Nunca inventa."""
    labels = labels or {}
    names  = source.variable_names()
    ranges = source.variable_ranges()
    out = []
    for v in names:
        info = labels.get(base_code(v))
        out.append({
            "name":           v,
            "label":          info["label"] if info else get_variable_label(v),
            "dimension":      info["dimension"] if info else dimension_of(v),
            "dimensionLabel": info["dimensionLabel"] if info else None,
            "range":          ranges.get(v),
        })
    return {"variables": out}


def get_counts(source: HexagonSource, zoom: int, selected: list[str],
               var_name: Optional[str] = None) -> dict:
    return source.counts(selected, var_name)


# ── Detalle ───────────────────────────────────────────────────────────────────

def get_detail(source: HexagonSource, h3_index: str) -> Optional[HexDetail]:
    hx = source.fetch_one(h3_index)
    if hx is None:
        return None
    # score_general de la master viene 1–5 (−1 = excluida) → normaliza a [0,1]
    # con (raw−1)/4 para que el popup lo muestre en % correcto (front hace ×100).
    norm = normalize_model_score(hx.score_general)
    # score_general == −1 → zona excluida (aunque la master no traiga tipo_celda).
    tipo = "excluida" if is_excluded_by_score(hx.score_general) else hx.tipo_celda
    return HexDetail(
        h3_index=hx.h3_index, score_general=norm if norm is not None else 0.0,
        tipo_celda=tipo, lat=hx.lat, lng=hx.lng, variables=hx.variables,
    )


# ── Tile (mapa) ───────────────────────────────────────────────────────────────

def get_tile(source: HexagonSource, z: int, x: int, y: int,
             var: Optional[str] = None) -> list[TileHex]:
    west, south, east, north = geo.tile_to_bbox(z, x, y)
    # STREAMING (no materializa el bbox): a zoom bajo un tile abarca millones de
    # hexágonos res 8 → `fetch_bbox` los cargaba todos en RAM y disparaba el OOM
    # del mapa. `aggregate` consume el generador y solo retiene los padres del zoom.
    rows = source.stream_bbox(west, south, east, north)         # base (res 8) en el bbox

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
        # score_general == −1 → zona excluida (no seleccionable en la cuadrícula).
        tipo = "excluida" if is_excluded_by_score(hx.score_general) else hx.tipo_celda
        out.append(TileHex(h=hx.h3_index, s=hx.score_general, t=tipo,
                           la=hx.lat, ln=hx.lng, v=value, off=off))
    return out


def excluded_cells(source: HexagonSource) -> list[str]:
    """Todos los h3 excluidos (capa completa) en UNA sola consulta → el front los
    cachea y deja de pedir `get_tile` por cada tile solo para marcar excluidos."""
    fn = getattr(source, "excluded_cells", None)
    return fn() if callable(fn) else []


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
    # STREAMING del área (cae a `fetch_cells` si la fuente no lo expone).
    stream_fn = getattr(source, "stream_cells", None)
    rows      = stream_fn(cells) if callable(stream_fn) else source.fetch_cells(cells)
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
# Resolución de agregación por defecto para una consulta a nivel PAÍS sin `res`
# (defensa anti-OOM): res 6 ≈ decenas de miles de hexágonos, no millones.
_COUNTRY_AGG_RES = 6


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
                       subtemplate: bool = False,
                       res: Optional[int] = None) -> list[dict]:
    """
    Capa de una variable en un bbox: `[{h3_index, value}]` con el valor normalizado
    0–1 donde la variable está presente. Sin bbox usa Colombia entera.

    MEMORIA (evita OOM): lee el bbox en STREAMING (no materializa millones de
    filas) y, si se pasa `res`, AGREGA al hexágono padre de esa resolución
    (promedio) → la respuesta es de miles de filas, no de millones. `res` = la
    resolución del zoom (el mapa ya pinta con resumen de padres).

    `subtemplate`: si el `code` no se reconoce, usa una variable disponible al
    azar (determinista) — modo puente hasta conocer el mapeo real de códigos.
    """
    b = bbox or _COLOMBIA_BBOX
    # DEFENSA anti-OOM: una consulta a nivel PAÍS sin `res` (p.ej. un front aún no
    # actualizado) agregaría millones de filas en `out` → forzamos una resolución
    # gruesa para que la respuesta y la memoria queden acotadas siempre.
    if res is None and bbox is None:
        res = _COUNTRY_AGG_RES
    available = source.variable_names()
    eff_code, is_sub = _resolve_code(available, code, subtemplate)
    if eff_code is None:
        return []  # código no reconocido y sin subtemplate → nada que pintar
    if is_sub:
        log.info("tiles/variable: '%s' no reconocido → subtemplate '%s'.", code, eff_code)
    var_range = source.variable_range(eff_code)
    rows = source.stream_bbox(b["west"], b["south"], b["east"], b["north"])

    if res is not None:
        # Agrega al padre `res` sobre la marcha: solo el dict agregado vive en RAM.
        agg: dict[str, tuple[float, int]] = {}
        for hx in rows:
            _, v = variable_engine.resolve_value(hx, eff_code, available, var_range, "normalized")
            if v is None or v < threshold:
                continue
            try:
                parent = h3.cell_to_parent(hx.h3_index, res)  # res≥8 → mismo hex
            except Exception:  # noqa: BLE001 — res más fina que el dato → sin agregar
                parent = hx.h3_index
            s, c = agg.get(parent, (0.0, 0))
            agg[parent] = (s + v, c + 1)
        return [{"h3_index": p, "value": s / c} for p, (s, c) in agg.items()]

    out: list[dict] = []
    for hx in rows:
        _, value_norm = variable_engine.resolve_value(
            hx, eff_code, available, var_range, "normalized"
        )
        if value_norm is None or value_norm < threshold:
            continue  # variable ausente en el hex o bajo el umbral
        out.append({"h3_index": hx.h3_index, "value": value_norm})
    return out


def get_variable_layers(source: HexagonSource, codes: list[str],
                        bbox: Optional[dict] = None,
                        threshold: float = 0.0,
                        subtemplate: bool = False,
                        res: Optional[int] = None) -> dict[str, list[dict]]:
    """BULK: varias variables en UNA sola pasada del bbox → escanea el stream UNA
    vez y resuelve TODOS los `codes` por hex (antes: una petición y un scan por
    variable → N scans que apilaban los hilos). Devuelve `{code: [{h3_index,value}]}`.
    Misma agregación por `res` que `get_variable_layer`."""
    if not codes:
        return {}
    b = bbox or _COLOMBIA_BBOX
    if res is None and bbox is None:
        res = _COUNTRY_AGG_RES
    available = source.variable_names()
    # Resuelve cada código una sola vez (código efectivo + rango).
    resolved: dict[str, tuple[str, Optional[dict]]] = {}
    for code in codes:
        eff, _is_sub = _resolve_code(available, code, subtemplate)
        if eff is not None:
            resolved[code] = (eff, source.variable_range(eff))
    result: dict[str, list[dict]] = {c: [] for c in codes}
    if not resolved:
        return result

    rows = source.stream_bbox(b["west"], b["south"], b["east"], b["north"])
    # agg[code] = {parent: (suma, conteo)}  (solo el agregado vive en RAM)
    agg: dict[str, dict[str, tuple[float, int]]] = {c: {} for c in resolved}
    flat: dict[str, list[dict]] = {c: [] for c in resolved}
    for hx in rows:
        for code, (eff_code, var_range) in resolved.items():
            _, v = variable_engine.resolve_value(hx, eff_code, available, var_range, "normalized")
            if v is None or v < threshold:
                continue
            if res is not None:
                try:
                    parent = h3.cell_to_parent(hx.h3_index, res)
                except Exception:  # noqa: BLE001
                    parent = hx.h3_index
                s, c = agg[code].get(parent, (0.0, 0))
                agg[code][parent] = (s + v, c + 1)
            else:
                flat[code].append({"h3_index": hx.h3_index, "value": v})
    for code in resolved:
        if res is not None:
            result[code] = [{"h3_index": p, "value": s / c} for p, (s, c) in agg[code].items()]
        else:
            result[code] = flat[code]
    return result


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

    # STREAMING del área: cada hex trae su JSONB completo; una selección amplia
    # cargaba todo a la vez. Cae a `fetch_cells` si la fuente no expone streaming
    # (p.ej. stub en memoria) — sin romper.
    stream_fn  = getattr(source, "stream_cells", None)
    rows       = stream_fn(cells) if callable(stream_fn) else source.fetch_cells(cells)
    # Ponderadas por hex (tabla aparte) para el "descargar crudos". Opcional: si la
    # fuente no la expone (p.ej. stub en memoria), queda vacío sin romper.
    pon_fn     = getattr(source, "fetch_variables_pon", None)
    pon        = pon_fn(cells) if callable(pon_fn) else {}
    available  = source.variable_names()
    ranges     = source.variable_ranges()
    # Agrupa por dimensión canónica (SOE se funde en Social junto con SOC).
    vars_by_cat: dict[str, list[str]] = {}
    for v in available:
        if "_" in v and len(v.split("_")[0]) <= 3:
            vars_by_cat.setdefault(_canon_cat(v.split("_")[0]), []).append(v)

    out: list[AreaHex] = []
    for hx in rows:
        scores: dict[str, float] = {}
        gen_num = 0.0  # Σ(peso_dim · score_dim) — solo para el fallback template
        gen_den = 0.0  # Σ peso_dim
        # score por DIMENSIÓN = promedio de las PONDERADAS (variables_pon) que caen
        # en esa dimensión. Las ponderadas YA vienen en 1–5 (peso ya aplicado por el
        # modelo), así que promediarlas da el 1–5 de la dimensión → se normaliza
        # (raw−1)/4 igual que el global. Es dato REAL de la master, sin pesos
        # template. Se agrupa por prefijo de categoría del código (TEC/AMB/SOC/SOE…).
        pon_vars = pon.get(hx.h3_index, {})
        pon_by_cat: dict[str, list[float]] = {}
        for var, pval in pon_vars.items():
            if pval is None or isinstance(pval, bool) or not isinstance(pval, (int, float)):
                continue
            cat = _canon_cat(var.split("_")[0])
            if 0 < len(cat) <= 3:
                pon_by_cat.setdefault(cat, []).append(float(pval))

        if pon_by_cat:
            # BACKEND real: promedio de ponderadas (1–5) por dimensión → [0,1].
            for cat, vals in pon_by_cat.items():
                norm_cat = normalize_model_score(sum(vals) / len(vals))
                if norm_cat is not None:
                    score_cat = round(norm_cat, 4)
                    scores[cat] = score_cat
        else:
            # TEMPLATE/stub (sin variables_pon): recompute WLC desde variables.
            for cat, cat_vars in vars_by_cat.items():
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

        # índice general = Σ(peso_dim · score_dim) / Σ peso_dim. El `global` de la
        # master viene en escala 1–5 (5 = más viable, −1 = excluida) → se NORMALIZA
        # a [0,1] aquí con (raw−1)/4. Los scores por dimensión de arriba ya salen en
        # [0,1] (se computan de variables normalizadas). Si no hay global válido
        # (None/−1), se cae al calculado.
        computed_general = round(gen_num / gen_den, 4) if gen_den > 0 else 0.0
        norm_general = normalize_model_score(hx.score_general)
        score_general = round(norm_general, 4) if norm_general is not None else computed_general

        # score_general == −1 → zona excluida (aunque falte tipo_celda en la master).
        tipo = "excluida" if is_excluded_by_score(hx.score_general) else hx.tipo_celda
        out.append(AreaHex(
            h3_index=hx.h3_index, lat=hx.lat, lng=hx.lng,
            score_general=score_general, scores=scores,
            tipo_celda=tipo, variables=hx.variables,
            variables_pon=pon.get(hx.h3_index, {}),
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
