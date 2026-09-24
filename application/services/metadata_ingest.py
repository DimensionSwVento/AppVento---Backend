"""
Actualizador de METADATOS del catálogo (Fase 1 del rediseño anidado).

Recibe un CSV/XLSX (p.ej. InfoVariables), **clasifica sus columnas** contra un
PERFIL (encabezado → campo destino), y **plan → conciliar → aplicar**:
- FASE A `plan`: lee cabeceras, dice qué columnas se mapean y cuáles quedan sin
  mapear (informativo; para metadatos NO bloquea — solo se omiten).
- FASE C `apply`: upsert de la jerarquía `dimension → criterio → subcriterio →
  variable`, **conciliando la variable por `master_id`** (nunca por nombre) para
  no tocar la variable equivocada. La jerarquía se concilia por `code`/nombre.

Perfiles: `infovariables` (embebido) + overrides en la tabla `column_mapping`
(BD). Fuente de verdad del catálogo = InfoVariables(2); la vieja solo se compara
para cambios de código (fuera de este servicio).
"""
from __future__ import annotations
import os
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import (
    CatalogVariable, ColumnMapping, Dimension, Criterio, Subcriterio, Norma,
)
from infrastructure.data.metadata_reader import (
    read_table, read_headers, list_sheets, forward_fill,
)


# ── Normalización de texto (acentos/espacios) ────────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def _slug(s: str) -> str:
    n = _norm(s)
    out = "".join(c if c.isalnum() else "-" for c in n)
    return "-".join(p for p in out.split("-") if p)


def _panel_name(name: str | None) -> str | None:
    """Nombre de panel a partir del nombre de archivo original (sin extensión)."""
    if not name:
        return None
    return os.path.splitext(os.path.basename(name))[0] or None


def _detect_kind(path: str) -> str:
    """Autodetecta el tipo de doc por sus encabezados (barato, solo cabeceras):
    'economic' si alguna hoja/columna trae `escenario_mw` + `valor` (panel macro);
    si no, 'infovariables' (catálogo). Un solo drag → se rutea solo."""
    def _is_eco(headers: list[str]) -> bool:
        hs = {h.strip().lower() for h in headers}
        return "escenario_mw" in hs and "valor" in hs

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm"):
            for sh in list_sheets(path):
                if _is_eco(read_headers(path, sheet=sh)):
                    return "economic"
            return "infovariables"
        if _is_eco(read_headers(path)):
            return "economic"
        return "infovariables"
    except Exception:  # noqa: BLE001 — ante la duda, catálogo
        return "infovariables"


# ── Perfil InfoVariables: (keywords, destino, transform). ORDEN IMPORTA
#    (subcriterio antes que criterio; url antes que fuente; mapa antes que menú). ─
_INFOVARIABLES_PROFILE: list[tuple[tuple[str, ...], str, str]] = [
    (("dimension",), "dimension", "texto"),
    (("subcriterio",), "subcriterio", "texto"),
    (("criterio",), "criterio", "texto"),
    (("emplazamiento",), "emplazamiento", "emplazamiento"),
    (("se muestra en el mapa", "mapa"), "en_mapa", "bool_si_no"),
    (("menu",), "tipo", "menu_tipo"),
    (("id",), "master_id", "texto"),
    (("descripcion",), "descripcion", "texto"),
    (("url",), "url_fuente", "texto"),
    (("tipo de licencia", "licencia"), "licencia", "texto"),
    (("fuente",), "fuente", "texto"),
    (("estado",), "estado", "texto"),               # regulatoria: estado de la norma
    (("condicion", "activacion"), "condicion", "texto"),  # regulatoria: condición de activación
    (("comentarios", "ideas"), "comentarios", "texto"),   # informativas: nota de visualización
    (("norma", "disposicion"), "label", "texto"),   # regulatoria: nombre de la norma
    (("variable",), "label", "texto"),
]

# Columnas de jerarquía a rellenar hacia abajo (vienen apiladas en InfoVariables).
_HIER_DESTINOS = ("dimension", "criterio", "subcriterio")


def _match_header(header: str) -> tuple[str, str] | None:
    """(destino, transform) del encabezado según el perfil, o None si no mapea."""
    h = _norm(header)
    if not h:
        return None
    for keywords, destino, transform in _INFOVARIABLES_PROFILE:
        for kw in keywords:
            if h == kw or h.startswith(kw + " ") or f" {kw} " in f" {h} " or kw in h:
                return destino, transform
    return None


def _classify(db: Session, perfil: str, header: str) -> tuple[str, str] | None:
    """Override de BD (`column_mapping`) primero; luego el perfil embebido."""
    row = db.get(ColumnMapping, f"{perfil}:{_norm(header)}")
    if row is not None:
        if row.estado == "ignorada":
            return None
        return row.destino, row.transform
    return _match_header(header)


# ── Transforms de valor ──────────────────────────────────────────────────────

def _to_bool_si_no(v: str) -> bool:
    return _norm(v) in ("si", "s", "true", "1", "x")


def _to_emplazamiento(v: str) -> str:
    n = _norm(v).replace(" ", "")
    if "onshore" in n and "offshore" in n:
        return "both"
    if "offshore" in n:
        return "offshore"
    if "onshore" in n:
        return "onshore"
    return "both"


def _menu_tipo(v: str) -> tuple[str, bool]:
    """('modelo'|'informativa', no_calculo). 'Activo por defecto por MCDM' = modelo."""
    n = _norm(v)
    if "mcdm" in n or "modelo" in n:
        return "modelo", False
    return "informativa", True


def _estado_norma(v: str) -> str:
    """Estado del semáforo regulatorio (catalog_norma): aplica|tramite|verificacion|no-aplica."""
    n = _norm(v)
    if "no aplica" in n:
        return "no-aplica"
    if "tramite" in n:
        return "tramite"
    if "verificac" in n:
        return "verificacion"
    if "vigente" in n or "aplica" in n:
        return "aplica"
    return "verificacion"


def _is_norma_row(master_id: str, dim_id: str) -> bool:
    """Fila regulatoria: código REG… o dimensión regulatoria → va a catalog_norma."""
    return master_id.upper().startswith("REG") or "regulac" in dim_id or "regulator" in dim_id


# ── FASE A — PLAN ─────────────────────────────────────────────────────────────

def plan(db: Session, path: str, perfil: str = "infovariables",
         sheet: str | None = None, name: str | None = None) -> dict:
    """Qué columnas del doc se mapean y cuáles se omiten (sin mapear).
    Autodetecta: si es panel económico, rutea a economic_ingest.plan. `name` = nombre
    original del archivo (para nombrar el panel cuando es un CSV económico suelto)."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    if _detect_kind(path) == "economic":
        from application.services import economic_ingest  # noqa: PLC0415
        p = economic_ingest.plan(path, default_panel=_panel_name(name))
        if "error" not in p:
            p["tipo"] = "economic"
        return p
    try:
        table = read_table(path, sheet=sheet)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    mapped: list[dict] = []
    unmapped: list[dict] = []
    for h in table.headers:
        m = _classify(db, perfil, h)
        if m:
            mapped.append({"header": h, "destino": m[0], "transform": m[1]})
        else:
            unmapped.append({"header": h})
    return {
        "tipo": "infovariables",
        "perfil": perfil, "sheet": table.sheet, "rows": len(table.rows),
        "mapped": mapped, "unmapped": unmapped,
        "summary": {"total": len(table.headers), "mapeadas": len(mapped),
                    "sin_mapear": len(unmapped)},
    }


# ── Conciliación del registro `column_mapping` ───────────────────────────────

def list_mappings(db: Session, perfil: str | None = None) -> list[dict]:
    stmt = select(ColumnMapping).order_by(ColumnMapping.id)
    if perfil:
        stmt = stmt.where(ColumnMapping.perfil == perfil)
    return [{"id": c.id, "perfil": c.perfil, "encabezado": c.encabezado,
             "destino": c.destino, "transform": c.transform, "estado": c.estado,
             "nota": c.nota} for c in db.scalars(stmt)]


def upsert_mapping(db: Session, perfil: str, encabezado: str, body: dict) -> dict:
    """Concilia un encabezado desconocido → campo destino del catálogo."""
    key = f"{perfil}:{_norm(encabezado)}"
    row = db.get(ColumnMapping, key)
    if row is None:
        row = ColumnMapping(id=key, perfil=perfil, encabezado=encabezado)
        db.add(row)
    for attr in ("destino", "transform", "estado", "nota"):
        if attr in body and body[attr] is not None:
            setattr(row, attr, body[attr])
    if not row.estado:
        row.estado = "ok"
    db.commit()
    db.refresh(row)
    return {"id": row.id, "destino": row.destino, "transform": row.transform,
            "estado": row.estado}


# ── FASE C — APLICAR (upsert de la jerarquía + variables) ────────────────────

def apply(db: Session, path: str, perfil: str = "infovariables",
          sheet: str | None = None, name: str | None = None) -> dict:
    """Upsert `dimension → criterio → subcriterio → variable` desde el doc.
    Concilia la variable por `master_id`; la jerarquía por nombre/`code`.
    Autodetecta: si es panel económico, rutea a economic_ingest.apply. `name` =
    nombre original del archivo (para nombrar el panel de un CSV económico suelto)."""
    if not os.path.exists(path):
        return {"error": f"no existe el archivo: {path}"}
    if _detect_kind(path) == "economic":
        from application.services import economic_ingest  # noqa: PLC0415
        r = economic_ingest.apply(db, path, only_curated=True,
                                  default_panel=_panel_name(name))
        if "error" not in r:
            r["tipo"] = "economic"
        return r
    try:
        table = read_table(path, sheet=sheet)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    # Encabezado → destino/transform (solo los mapeados).
    cols: dict[str, tuple[str, str]] = {}
    for h in table.headers:
        m = _classify(db, perfil, h)
        if m:
            cols[h] = m
    # Encabezado de cada destino (para leer el valor de la fila).
    dest_header: dict[str, str] = {}
    for h, (dest, _t) in cols.items():
        dest_header.setdefault(dest, h)

    # Rellena la jerarquía apilada (Dimensión/Criterio/Subcriterio).
    hier_headers = [dest_header[d] for d in _HIER_DESTINOS if d in dest_header]
    rows = forward_fill(table.rows, hier_headers)

    # Definición por nivel = la columna SIN mapear justo DESPUÉS del nivel
    # (InfoVariables intercala "Definición" tras Dimensión/Criterio/Subcriterio).
    def_header: dict[str, str] = {}
    for dest in _HIER_DESTINOS:
        h = dest_header.get(dest)
        if not h or h not in table.headers:
            continue
        idx = table.headers.index(h)
        if idx + 1 < len(table.headers):
            nxt = table.headers[idx + 1]
            if _classify(db, perfil, nxt) is None:  # sin mapear → es su definición
                def_header[dest] = nxt

    def raw(row: dict, header: str | None) -> str:
        return str(row.get(header) or "").strip() if header else ""

    n_dim = n_cri = n_sub = n_var = n_norma = 0
    seen_dim: set[str] = set()
    seen_cri: set[str] = set()
    seen_sub: set[str] = set()

    def val(row: dict, dest: str) -> str:
        h = dest_header.get(dest)
        return str(row.get(h) or "").strip() if h else ""

    for row in rows:
        dim_name = val(row, "dimension")
        cri_name = val(row, "criterio")
        sub_name = val(row, "subcriterio")
        label = val(row, "label")
        master_id = val(row, "master_id")

        # Fila separadora / vacía: sin variable NI master_id → se ignora.
        if not label and not master_id:
            continue

        # REGULATORIA → catalog_norma (no catalog_variable). Se rutea por código
        # REG… o dimensión regulatoria, ANTES de crear jerarquía/variable.
        if master_id and _is_norma_row(master_id, _slug(dim_name) if dim_name else ""):
            desc = val(row, "descripcion")
            nom = label or (desc[:80] if desc else master_id)
            nrm = db.get(Norma, master_id)
            if nrm is None:
                nrm = Norma(id=master_id, nombre=nom)
                db.add(nrm)
                n_norma += 1
            elif nom:
                nrm.nombre = nom
            if desc:
                nrm.descripcion = desc
            est = val(row, "estado")
            if est:
                nrm.estado = _estado_norma(est)
            # Metadatos regulatorios (antes se perdían).
            for dest, attr in (("fuente", "fuente"), ("url_fuente", "url_fuente"),
                               ("condicion", "condicion")):
                v = val(row, dest)
                if v:
                    setattr(nrm, attr, v)
            # Regulación-Social/Técnica/Ambiental: la norma va ligada a una VARIABLE
            # (código SOC/AMB/…, no REG) → se guarda la referencia.
            nrm.variable_ref = "" if master_id.upper().startswith("REG") else master_id
            continue

        # Jerarquía (upsert por slug estable).
        dim_def = raw(row, def_header.get("dimension"))
        cri_def = raw(row, def_header.get("criterio"))
        sub_def = raw(row, def_header.get("subcriterio"))

        dim_id = _slug(dim_name) if dim_name else ""
        if dim_id and dim_id not in seen_dim:
            seen_dim.add(dim_id)
            d = db.get(Dimension, dim_id)
            if d is None:
                d = Dimension(id=dim_id, nombre=dim_name, definicion=dim_def)
                db.add(d)
                n_dim += 1
            else:
                if not d.nombre:
                    d.nombre = dim_name
                if dim_def and not d.definicion:
                    d.definicion = dim_def

        cri_id = f"{dim_id}:{_slug(cri_name)}" if cri_name else ""
        if cri_id and cri_id not in seen_cri:
            seen_cri.add(cri_id)
            c = db.get(Criterio, cri_id)
            if c is None:
                c = Criterio(id=cri_id, dimension_id=dim_id, nombre=cri_name, definicion=cri_def)
                db.add(c)
                n_cri += 1
            elif cri_def and not c.definicion:
                c.definicion = cri_def

        sub_id = f"{cri_id}:{_slug(sub_name)}" if sub_name else ""
        if sub_id and sub_id not in seen_sub:
            seen_sub.add(sub_id)
            s = db.get(Subcriterio, sub_id)
            if s is None:
                s = Subcriterio(id=sub_id, criterio_id=cri_id, nombre=sub_name, definicion=sub_def)
                db.add(s)
                n_sub += 1
            elif sub_def and not s.definicion:
                s.definicion = sub_def

        # Variable: conciliar por master_id (no por nombre).
        if not master_id or master_id.upper() in ("NA", ""):
            continue
        var = db.scalars(
            select(CatalogVariable).where(CatalogVariable.master_id == master_id)
        ).first()
        if var is None:
            # id (PK) desde el master_id → único (el slug del label choca: p.ej.
            # "Tasa de analfabetismo" está en SOE-004 y SOC-012). label/dimension
            # son NOT NULL → se pasan en el constructor.
            var = CatalogVariable(
                id=_slug(master_id), master_id=master_id,
                label=label or master_id, dimension=dim_id or "",
            )
            db.add(var)
            n_var += 1

        # Campos descriptivos desde el doc (solo si vienen).
        if label:
            var.label = label
        if dim_id:
            var.dimension = dim_id
        if cri_name:
            var.criterio = cri_name
        if sub_name:
            var.subcriterio = sub_name
        if sub_id:
            var.subcriterio_id = sub_id
        for dest, attr in (("descripcion", "descripcion"), ("fuente", "fuente"),
                           ("licencia", "licencia"), ("url_fuente", "url_fuente"),
                           ("comentarios", "comentarios")):
            v = val(row, dest)
            if v:
                setattr(var, attr, v)
        if "emplazamiento" in dest_header:
            var.emplazamiento = _to_emplazamiento(val(row, "emplazamiento"))
        if "en_mapa" in dest_header:
            var.en_mapa = _to_bool_si_no(val(row, "en_mapa"))
        if "tipo" in dest_header:
            tipo, no_calc = _menu_tipo(val(row, "tipo"))
            var.tipo = tipo
            var.clase = "modelo" if tipo == "modelo" else "informativa"
            var.no_calculo = no_calc

    db.commit()
    return {"tipo": "infovariables", "dimensiones": n_dim, "criterios": n_cri,
            "subcriterios": n_sub, "variables": n_var, "normas": n_norma,
            "perfil": perfil, "sheet": table.sheet}


# ── Lectura de la jerarquía (para la app / página de Dimensiones) ─────────────

def hierarchy(db: Session, emplazamiento: str | None = None) -> list[dict]:
    """Catálogo anidado `dimension → criterio → subcriterio → variable`.
    Filtra variables por `emplazamiento` (incluye siempre `both`) si se pide."""
    dims = list(db.scalars(select(Dimension).order_by(Dimension.orden, Dimension.id)))
    cris = list(db.scalars(select(Criterio).order_by(Criterio.id)))
    subs = list(db.scalars(select(Subcriterio).order_by(Subcriterio.id)))
    vars_ = list(db.scalars(select(CatalogVariable)))
    if emplazamiento in ("onshore", "offshore"):
        vars_ = [v for v in vars_ if v.emplazamiento in (emplazamiento, "both")]

    vars_by_sub: dict[str, list[dict]] = {}
    for v in vars_:
        vars_by_sub.setdefault(v.subcriterio_id, []).append({
            "id": v.id, "masterId": v.master_id, "label": v.label,
            "descripcion": v.descripcion, "fuente": v.fuente, "licencia": v.licencia,
            "url": v.url_fuente, "emplazamiento": v.emplazamiento, "enMapa": v.en_mapa,
            "tipo": v.tipo, "clase": v.clase, "iconKey": v.icon_key,
            "peso": v.peso, "polaridad": v.polaridad,
        })
    subs_by_cri: dict[str, list[dict]] = {}
    for s in subs:
        subs_by_cri.setdefault(s.criterio_id, []).append({
            "id": s.id, "code": s.code, "nombre": s.nombre, "definicion": s.definicion,
            "variables": vars_by_sub.get(s.id, []),
        })
    cris_by_dim: dict[str, list[dict]] = {}
    for c in cris:
        cris_by_dim.setdefault(c.dimension_id, []).append({
            "id": c.id, "code": c.code, "nombre": c.nombre, "definicion": c.definicion,
            "peso": c.peso, "subcriterios": subs_by_cri.get(c.id, []),
        })
    return [{
        "id": d.id, "nombre": d.nombre, "definicion": d.definicion, "puntua": d.puntua,
        "criterios": cris_by_dim.get(d.id, []),
    } for d in dims]
