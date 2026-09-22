"""
Ingesta del bundle ECONÓMICO macro (Fase 3). Lee `panel_por_variable.xlsx`
(26 hojas, formato tidy `escenario_mw | cat1..catN | anio | valor`) → tabla LONG
`economic_series`, y la hoja RESUMEN → catálogo `economic_panel`.

Informativa: no toca `hexagon` ni el modelo de idoneidad. La app muestra un
subconjunto CURADO (RESUMEN1/PIB/PRECIOS/GENERACION/EMISIONES/EMPLEO); el resto se
almacena para trazabilidad. Ver docs/spec/modelo-datos-anidado.md §6.3.
"""
from __future__ import annotations
import os

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from infrastructure.db.models import EconomicPanel, EconomicSeries
from infrastructure.data.metadata_reader import list_sheets, read_table

# Hojas que la app muestra en el panel económico (US04-02). El resto = solo BD.
CURATED = {"RESUMEN1", "PIB", "PRECIOS", "GENERACION", "EMISIONES", "EMPLEO"}
_META_SHEET = "RESUMEN"


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

def plan(path: str) -> dict:
    """Lista las hojas del xlsx y marca cuáles son curadas / meta."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    try:
        sheets = list_sheets(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    out = []
    for s in sheets:
        up = _norm_sheet(s)
        out.append({
            "sheet": s,
            "rol": "meta" if up == _META_SHEET else ("curada" if up in CURATED else "solo-bd"),
        })
    return {"sheets": out, "total": len(sheets),
            "curadas": sum(1 for s in out if s["rol"] == "curada")}


# ── APPLY ─────────────────────────────────────────────────────────────────────

def apply(db: Session, path: str, sheets: list[str] | None = None,
          only_curated: bool = True, batch_size: int = 5000) -> dict:
    """Ingiere el xlsx. Por defecto SOLO las hojas curadas (las que ve la app);
    `only_curated=False` ingiere todas (incluye RESULTADOSPOT, ~453k filas)."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    try:
        all_sheets = list_sheets(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    # Catálogo de hojas desde RESUMEN (si está).
    meta = {_norm_sheet(s): s for s in all_sheets}
    if _META_SHEET in meta:
        _upsert_panels(db, path, meta[_META_SHEET])

    targets = sheets or [s for s in all_sheets if _norm_sheet(s) != _META_SHEET]
    if only_curated:
        targets = [s for s in targets if _norm_sheet(s) in CURATED]

    total = 0
    done: list[str] = []
    for sheet in targets:
        n = _ingest_sheet(db, path, sheet, batch_size)
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


def _ingest_sheet(db: Session, path: str, sheet: str, batch_size: int) -> int:
    """Una hoja tidy → economic_series (reemplaza las filas previas de ese panel)."""
    t = read_table(path, sheet=sheet)
    esc_h = _find_header(t.headers, ("escenario_mw", "escenario"))
    anio_h = _find_header(t.headers, ("anio", "año", "year"))
    val_h = _find_header(t.headers, ("valor", "value"))
    cat_hs = [h for h in t.headers if h.strip().lower().startswith("cat")]
    if not val_h:
        return 0

    # Idempotente: borra las filas previas de este panel antes de recargar.
    db.execute(delete(EconomicSeries).where(EconomicSeries.panel == sheet))
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
            "panel": sheet,
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
