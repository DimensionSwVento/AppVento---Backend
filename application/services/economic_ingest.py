"""
Ingesta del bundle ECONÓMICO macro (Fase 3). Cubre los TRES archivos de la
dimensión económica → tabla LONG única `economic_series`:

1. `panel_por_variable.xlsx` — OUTPUTS del modelo (26 hojas, formato **tidy**
   `escenario_mw | cat1..catN | anio | valor`); la hoja RESUMEN → `economic_panel`.
2. `variables_esce.xlsx` / `VARIABLES_2.xlsx` — INPUTS de escenario (drivers
   exógenos), formato **ancho** `Variable | YEAR | Escenario_mw_<N>…`. Se
   detectan por COLUMNAS (el nombre de archivo puede cambiar) y se derriten
   (melt) a la misma tabla long: `cat1 = Variable`, `escenario_mw = <N>`.

Informativa: no toca `hexagon` ni el modelo de idoneidad. La app muestra un
subconjunto CURADO (RESUMEN1/PIB/PRECIOS/GENERACION/EMISIONES/EMPLEO); el resto se
almacena para trazabilidad. Ver docs/spec/modelo-datos-anidado.md §6.3.
"""
from __future__ import annotations
import os

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infrastructure.db.models import EconomicPanel, EconomicSeries
from infrastructure.data.metadata_reader import list_sheets, read_headers, read_table

# Hojas que la app muestra en el panel económico (US04-02). El resto = solo BD.
CURATED = {"RESUMEN1", "PIB", "PRECIOS", "GENERACION", "EMISIONES", "EMPLEO"}
_META_SHEET = "RESUMEN"


def _sheet_format(headers: list[str]) -> str:
    """Formato de una hoja/archivo por sus columnas:
    - 'wide'  = inputs de escenario (columnas `Escenario_mw_<N>` → melt).
    - 'tidy'  = panel de salida (`escenario_mw` + `valor`).
    - 'meta'  = RESUMEN u otra (catálogo de paneles / no ingerible)."""
    hs = {h.strip().lower() for h in headers}
    if any(h.startswith("escenario_mw_") for h in hs):
        return "wide"
    if "valor" in hs and "escenario_mw" in hs:
        return "tidy"
    return "meta"


def _esc_from_header(h: str) -> int:
    """`Escenario_mw_1000` → 1000 (el MW de generación va en el nombre de columna)."""
    return _to_int(h.strip().lower().replace("escenario_mw_", ""))


def _to_int(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def _to_float(v):
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def _norm_sheet(name: str) -> str:
    return (name or "").strip().upper()


# ── PLAN ──────────────────────────────────────────────────────────────────────

def _rol_sheet(sheet: str, fmt: str) -> str:
    """Rol para el plan (chip de la UI): input (ancho) / curada / meta / solo-bd."""
    if fmt == "wide":
        return "input"
    up = _norm_sheet(sheet)
    if up == _META_SHEET:
        return "meta"
    return "curada" if up in CURATED else "solo-bd"


def plan(path: str, default_panel: str | None = None) -> dict:
    """Lista las hojas (xlsx) o la única 'hoja' (csv) y su rol/formato. `default_panel`
    = nombre a usar como panel cuando es un archivo suelto (p.ej. el del archivo)."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    # CSV: una sola 'hoja' (openpyxl NO lee CSV → no se llama list_sheets).
    if os.path.splitext(path)[1].lower() == ".csv":
        name = default_panel or os.path.splitext(os.path.basename(path))[0]
        fmt = _sheet_format(read_headers(path))
        rol = _rol_sheet(name, fmt)
        return {"sheets": [{"sheet": name, "rol": rol}], "total": 1,
                "curadas": 1 if rol in ("curada", "input") else 0}
    try:
        sheets = list_sheets(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    out = []
    for s in sheets:
        fmt = _sheet_format(read_headers(path, sheet=s))
        out.append({"sheet": s, "rol": _rol_sheet(s, fmt)})
    return {"sheets": out, "total": len(sheets),
            "curadas": sum(1 for s in out if s["rol"] in ("curada", "input"))}


# ── APPLY ─────────────────────────────────────────────────────────────────────

def apply(db: Session, path: str, sheets: list[str] | None = None,
          only_curated: bool = True, batch_size: int = 5000,
          default_panel: str | None = None) -> dict:
    """Ingiere el xlsx (por hoja) o un CSV suelto (una hoja única). Por defecto solo
    las hojas curadas; `only_curated=False` ingiere todas. `default_panel` = nombre
    del panel cuando es CSV (p.ej. el nombre del archivo)."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    # CSV: se ingiere tal cual como un panel único (no aplica el filtro de hojas
    # curadas; openpyxl NO lee CSV). Puede ser tidy o ancho.
    if os.path.splitext(path)[1].lower() == ".csv":
        name = default_panel or os.path.splitext(os.path.basename(path))[0]
        fmt = _sheet_format(read_headers(path))
        n = (_ingest_wide(db, path, None, name, batch_size) if fmt == "wide"
             else _ingest_sheet(db, path, None, batch_size, panel=name))
        return {"panels": 1 if n else 0, "rows": n,
                "sheets": [f"{name}({n})"], "only_curated": False}
    try:
        all_sheets = list_sheets(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    # Catálogo de hojas desde RESUMEN (si está).
    meta = {_norm_sheet(s): s for s in all_sheets}
    if _META_SHEET in meta:
        _upsert_panels(db, path, meta[_META_SHEET])

    targets = sheets or [s for s in all_sheets if _norm_sheet(s) != _META_SHEET]

    total = 0
    done: list[str] = []
    for sheet in targets:
        fmt = _sheet_format(read_headers(path, sheet=sheet))
        if fmt == "wide":
            # Inputs de escenario: SIEMPRE se ingieren (no aplica curadas). El panel
            # se nombra con el archivo (Hoja1 no distingue variables_esce de VARIABLES_2).
            panel = default_panel or sheet
            n = _ingest_wide(db, path, sheet, panel, batch_size)
        elif fmt == "tidy":
            if only_curated and _norm_sheet(sheet) not in CURATED:
                continue
            n = _ingest_sheet(db, path, sheet, batch_size)
        else:
            continue  # 'meta'/no ingerible
        total += n
        done.append(f"{sheet}({n})")
    return {"panels": len(done), "rows": total, "sheets": done,
            "only_curated": only_curated}


def _upsert_panels(db: Session, path: str, resumen_sheet: str) -> None:
    """RESUMEN → economic_panel (id/nombre/descripción + flag curado)."""
    t = read_table(path, sheet=resumen_sheet)
    # Encabezados esperados: Variable, Descripción (pueden variar de mayúsculas).
    var_h = _find_header(t.headers, ("variable",))
    desc_h = _find_header(t.headers, ("descripcion", "descripción"))
    if not var_h:
        return
    for r in t.rows:
        name = str(r.get(var_h) or "").strip()
        if not name or name.upper() in ("NOTAS", ""):
            continue
        pid = name
        p = db.get(EconomicPanel, pid)
        if p is None:
            p = EconomicPanel(id=pid)
            db.add(p)
        p.nombre = name
        if desc_h:
            p.descripcion = str(r.get(desc_h) or "").strip()
        p.curado = _norm_sheet(name) in CURATED
    db.commit()


def _find_header(headers: list[str], keys: tuple[str, ...]) -> str | None:
    for h in headers:
        hl = h.strip().lower()
        for k in keys:
            if hl == k or hl.startswith(k):
                return h
    return None


def _ingest_sheet(db: Session, path: str, sheet: str | None, batch_size: int,
                  panel: str | None = None) -> int:
    """Una hoja/CSV tidy → economic_series (reemplaza las filas previas del panel).
    `panel` = nombre del panel (para CSV, donde `sheet` es None)."""
    t = read_table(path, sheet=sheet)
    panel_name = panel or sheet or "IMPORT"
    esc_h = _find_header(t.headers, ("escenario_mw", "escenario"))
    anio_h = _find_header(t.headers, ("anio", "año", "year"))
    val_h = _find_header(t.headers, ("valor", "value"))
    cat_hs = [h for h in t.headers if h.strip().lower().startswith("cat")]
    if not val_h:
        return 0

    # Idempotente: borra las filas previas de este panel antes de recargar.
    db.execute(delete(EconomicSeries).where(EconomicSeries.panel == panel_name))
    db.commit()

    total = 0
    batch: list[dict] = []
    for r in t.rows:
        val = _to_float(r.get(val_h))
        if val is None:
            continue
        cats = [str(r.get(h) or "").strip() for h in cat_hs[:3]]
        cats += [""] * (3 - len(cats))
        batch.append({
            "panel": panel_name,
            "escenario_mw": _to_int(r.get(esc_h)) if esc_h else 0,
            "cat1": cats[0], "cat2": cats[1], "cat3": cats[2],
            "anio": _to_int(r.get(anio_h)) if anio_h else 0,
            "valor": val,
        })
        if len(batch) >= batch_size:
            db.bulk_insert_mappings(EconomicSeries, batch)
            db.commit()
            total += len(batch)
            batch = []
    if batch:
        db.bulk_insert_mappings(EconomicSeries, batch)
        db.commit()
        total += len(batch)
    return total


def _ingest_wide(db: Session, path: str, sheet: str | None, panel: str,
                 batch_size: int) -> int:
    """Inputs de escenario en formato ANCHO (`Variable | YEAR | Escenario_mw_<N>…`)
    → economic_series (melt): una fila por (Variable, YEAR, escenario). El MW sale
    del nombre de columna. Idempotente: reemplaza las filas previas del panel."""
    t = read_table(path, sheet=sheet)
    var_h = _find_header(t.headers, ("variable",))
    year_h = _find_header(t.headers, ("year", "anio", "año"))
    esc_cols = [(h, _esc_from_header(h)) for h in t.headers
                if h.strip().lower().startswith("escenario_mw_")]
    if not esc_cols or not year_h:
        return 0

    db.execute(delete(EconomicSeries).where(EconomicSeries.panel == panel))
    db.commit()
    _upsert_input_panel(db, panel)

    total = 0
    batch: list[dict] = []
    for r in t.rows:
        var = str(r.get(var_h) or "").strip() if var_h else ""
        anio = _to_int(r.get(year_h))
        for h, mw in esc_cols:
            val = _to_float(r.get(h))
            if val is None:
                continue
            batch.append({
                "panel": panel, "escenario_mw": mw,
                "cat1": var, "cat2": "", "cat3": "",
                "anio": anio, "valor": val,
            })
            if len(batch) >= batch_size:
                db.bulk_insert_mappings(EconomicSeries, batch)
                db.commit()
                total += len(batch)
                batch = []
    if batch:
        db.bulk_insert_mappings(EconomicSeries, batch)
        db.commit()
        total += len(batch)
    return total


def _upsert_input_panel(db: Session, panel: str) -> None:
    """Registra el panel de inputs en economic_panel (no curado; es driver exógeno)."""
    p = db.get(EconomicPanel, panel)
    if p is None:
        p = EconomicPanel(id=panel)
        db.add(p)
    p.nombre = panel
    if not p.descripcion:
        p.descripcion = "Inputs de escenario (drivers exógenos del modelo)"
    p.curado = False
    db.commit()


# ── LECTURA (panel económico de la app) ───────────────────────────────────────

def list_panels(db: Session, only_curated: bool = False) -> list[dict]:
    stmt = select(EconomicPanel).order_by(EconomicPanel.id)
    if only_curated:
        stmt = stmt.where(EconomicPanel.curado.is_(True))
    return [{"id": p.id, "nombre": p.nombre, "descripcion": p.descripcion,
             "curado": p.curado} for p in db.scalars(stmt)]


def series(db: Session, panel: str, escenario_mw: int | None = None,
           limit: int = 5000) -> list[dict]:
    """Serie de un panel (opcionalmente filtrada por escenario_mw)."""
    stmt = select(EconomicSeries).where(EconomicSeries.panel == panel)
    if escenario_mw is not None:
        stmt = stmt.where(EconomicSeries.escenario_mw == escenario_mw)
    stmt = stmt.order_by(EconomicSeries.anio).limit(limit)
    return [{"panel": s.panel, "escenarioMw": s.escenario_mw,
             "cat1": s.cat1, "cat2": s.cat2, "cat3": s.cat3,
             "anio": s.anio, "valor": s.valor} for s in db.scalars(stmt)]


def scenarios(db: Session, panel: str) -> list[int]:
    """Escenarios de MW disponibles para un panel (para el selector de la app)."""
    return sorted(
        db.scalars(
            select(EconomicSeries.escenario_mw)
            .where(EconomicSeries.panel == panel)
            .group_by(EconomicSeries.escenario_mw)
        )
    )
