"""
Genera `infrastructure/data/variables_seed.json` a partir del catálogo del
frontend (`variablesCatalog.ts`): VARIABLES → "modelo", OPTIONAL_VARIABLES →
"informativa". Reejecutar cuando cambie el catálogo del front, o reemplazar por
el catálogo real del equipo de datos.

Uso:  python -m tools.gen_variables_seed
"""
from __future__ import annotations
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
# El front vive junto al backend en el monorepo (../../Vento).
_TS = _ROOT.parent.parent / "Vento/src/app/Utils/variables/variablesCatalog.ts"
_WEIGHTS_TS = _ROOT.parent.parent / "Vento/src/app/Utils/variables/variableWeights.ts"
_OUT = _ROOT / "infrastructure/data/variables_seed.json"


def _load_weights() -> dict:
    """`VARIABLE_WEIGHTS` (Record<string, number>) → {id: peso_con_signo}."""
    if not _WEIGHTS_TS.exists():
        return {}
    text = _WEIGHTS_TS.read_text(encoding="utf-8")
    body = _array_body_obj(text, "VARIABLE_WEIGHTS")
    out: dict = {}
    for m in re.finditer(r'"([^"]+)"\s*:\s*(-?\d+(?:\.\d+)?)', body):
        out[m.group(1)] = float(m.group(2))
    return out


def _array_body_obj(text: str, name: str) -> str:
    m = re.search(rf"export const {name}\s*:[^=]*=\s*\{{(.*?)\}};", text, re.S)
    return m.group(1) if m else ""

_DIM_LABEL = {
    "tecnica": "Técnica", "ambiental": "Ambiental", "social": "Social",
    "macroeconomica": "Económica", "regulatoria": "Regulatoria",
}


def _array_body(text: str, name: str) -> str:
    m = re.search(rf"export const {name}\s*:[^=]*=\s*\[(.*?)\];", text, re.S)
    return m.group(1) if m else ""


def _field(obj: str, key: str):
    m = re.search(rf'\b{key}\s*:\s*"([^"]*)"', obj)
    if m:
        return m.group(1)
    m = re.search(rf'\b{key}\s*:\s*(true|false)\b', obj)
    if m:
        return m.group(1) == "true"
    return None


def _to_entry(obj: str, tipo: str, weights: dict) -> dict:
    dim = _field(obj, "dimension") or ""
    en_mapa = bool(_field(obj, "enMapa")) if _field(obj, "enMapa") is not None else False
    excl = bool(_field(obj, "excluyente")) if _field(obj, "excluyente") is not None else False
    no_calc = bool(_field(obj, "noCalculo")) if _field(obj, "noCalculo") is not None else (tipo == "informativa")
    vid = _field(obj, "id")
    w = weights.get(vid, 0.0)
    # map_kind: excluyentes se pintan como ZONA; el resto con dato en mapa, HEATMAP.
    map_kind = "zona" if excl else ("heatmap" if en_mapa else "")
    return {
        "id": vid,
        "label": _field(obj, "label") or "",
        "dimension": dim,
        "dimension_label": _DIM_LABEL.get(dim, ""),
        "tipo": tipo,
        "enabled": True,
        "en_mapa": en_mapa,
        "en_menu": tipo == "informativa",
        "excluyente": excl,
        "fuente": _field(obj, "fuente") or "Por definir",
        "unidad": _field(obj, "unidad") or "",
        "rango": _field(obj, "rango") or "",
        "descripcion": _field(obj, "description") or "",
        # ── Reales por-variable (antes hardcode) ──
        "master_id": _field(obj, "masterId") or "NA",
        "criterio": _field(obj, "criterio") or "",
        "subcriterio": _field(obj, "subcriterio") or "",
        "aplica_en": _field(obj, "aplicaEn") or "",
        "no_calculo": no_calc,
        "polaridad": (1 if w > 0 else -1 if w < 0 else 0),
        "peso": abs(w),
        "map_kind": map_kind,
        "map_threshold": 0.0,
    }


def main() -> int:
    text = _TS.read_text(encoding="utf-8")
    weights = _load_weights()
    objs_model = re.findall(r"\{[^{}]*\}", _array_body(text, "VARIABLES"))
    objs_info = re.findall(r"\{[^{}]*\}", _array_body(text, "OPTIONAL_VARIABLES"))
    entries = (
        [_to_entry(o, "modelo", weights) for o in objs_model]
        + [_to_entry(o, "informativa", weights) for o in objs_info]
    )
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        if not e["id"] or e["id"] in seen:
            continue
        seen.add(e["id"])
        out.append(e)
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(objs_model)} modelo + {len(objs_info)} informativa → {len(out)} únicas → {_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
