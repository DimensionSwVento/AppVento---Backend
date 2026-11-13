"""
Búsqueda de lugares PROPIA (sin Nominatim): lee `geo/geocodes-colombia.json`
(generado una vez desde OpenStreetMap vía el botón de descarga del front) y busca
por nombre, insensible a mayúsculas/tildes. Cero dependencia de terceros en runtime.

El archivo se carga UNA vez y se cachea en memoria (es ~1 MB / ~15k lugares).
"""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

_GEO_FILE = Path(__file__).resolve().parents[1] / "geo" / "geocodes-colombia.json"

# Orden de preferencia por tipo (una ciudad pesa más que un caserío homónimo).
_TYPE_RANK = {
    "city": 0,
    "municipality": 1,
    "town": 2,
    "village": 3,
    "suburb": 4,
    "hamlet": 5,
}


def _norm(s: str) -> str:
    """minúsculas sin tildes (para comparar 'Medellín' == 'medellin')."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


@lru_cache(maxsize=1)
def _entries() -> list[tuple[str, str, float, float, str]]:
    """(name, name_normalizado, lat, lng, type). Cacheado en memoria."""
    try:
        data = json.loads(_GEO_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    out: list[tuple[str, str, float, float, str]] = []
    for d in data:
        name = (d.get("name") or "").strip()
        if not name or name == "-sin nombre-":
            continue
        try:
            lat = float(d["lat"])
            lng = float(d["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((name, _norm(name), lat, lng, d.get("type", "place")))
    return out


def search(q: str, limit: int = 7) -> list[dict]:
    """Lugares cuyo nombre coincide con `q` (exacto > prefijo > subcadena), ordenados
    por coincidencia y tipo. Devuelve `[{name, lat, lng, type}]`."""
    nq = _norm((q or "").strip())
    if len(nq) < 2:
        return []
    scored: list[tuple[int, int, int, str, float, float, str]] = []
    for name, nn, lat, lng, typ in _entries():
        if nn == nq:
            match = 0
        elif nn.startswith(nq):
            match = 1
        elif nq in nn:
            match = 2
        else:
            continue
        scored.append((match, _TYPE_RANK.get(typ, 9), len(nn), name, lat, lng, typ))
    scored.sort()
    return [
        {"name": n, "lat": la, "lng": lo, "type": t}
        for _, _, _, n, la, lo, t in scored[:limit]
    ]
