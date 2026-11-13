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

# Umbral de dedup geográfico (grados ~ 0.4° ≈ 44 km): dos entradas homónimas más
# cercanas que esto son el mismo lugar repetido por OSM; más lejos = lugares distintos.
_DUP_DEG = 0.4

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
    por coincidencia y tipo, SIN duplicados. Devuelve `[{name, lat, lng, type}]`.

    Dedup: OSM trae el mismo lugar varias veces (p. ej. 5 "Medellín" casi en el
    mismo punto). Se colapsan por nombre normalizado cuando caen cerca (≤ ~0.4°, un
    mismo municipio), conservando el de mejor tipo; se mantienen como entradas
    distintas los homónimos realmente lejanos (dos "San José" en deptos. distintos)."""
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

    # Dedup geográfico: el primero (mejor rankeado) de cada nombre gana; se descarta
    # otro homónimo solo si cae a ≤ _DUP_DEG del ya conservado (mismo lugar repetido).
    kept: list[tuple[str, float, float, str]] = []  # (name_norm, lat, lng, name, type)
    out: list[dict] = []
    for _, _, _, name, lat, lng, typ in scored:
        nn = _norm(name)
        dup = any(
            knn == nn and abs(klat - lat) <= _DUP_DEG and abs(klng - lng) <= _DUP_DEG
            for knn, klat, klng, _ in kept
        )
        if dup:
            continue
        kept.append((nn, lat, lng, name))
        out.append({"name": name, "lat": lat, "lng": lng, "type": typ})
        if len(out) >= limit:
            break
    return out
