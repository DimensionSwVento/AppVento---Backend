"""
Operaciones de base de datos sobre `hexagon` (espejo persistido del parquet).

Dos vías de actualización, según lo acordado:
- `replace_all`: ingesta DESTRUCTIVA (borra todo y recarga). Para las muestras del
  modelo mientras llega el dataset definitivo.
- CRUD (`get` / `upsert` / `update` / `delete`): mantenimiento incremental una vez
  la tabla es la fuente de verdad.

`load_for_warmup` reconstruye lo que antes devolvía `read_parquet`, para alimentar
el índice H3 en memoria desde la BD.
"""
from __future__ import annotations
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from domain.models.hexagon import HexagonData
from infrastructure.db.models import Hexagon, HexagonMeta

# Orden de columnas para la ingesta masiva (COPY / bulk). `updated_at` se fija a un
# único timestamp por corrida.
_COPY_COLS = ("h3_index", "score_general", "tipo_celda", "lat", "lng", "variables", "updated_at")


# ── Sanitizado ────────────────────────────────────────────────────────────────

def _clean_variables(variables: dict | None) -> dict:
    """
    Normaliza los valores del parquet a tipos JSON nativos (pandas/numpy pueden
    traer np.float64, np.int64, np.nan…). JSONB no serializa escalares numpy.
    """
    out: dict[str, str | float | None] = {}
    for k, v in (variables or {}).items():
        if v is None:
            out[str(k)] = None
        elif hasattr(v, "item"):          # escalares numpy → python nativo
            native = v.item()
            out[str(k)] = None if native != native else native  # NaN → None
        elif isinstance(v, float) and v != v:  # NaN
            out[str(k)] = None
        else:
            out[str(k)] = v
    return out


def _to_row(data: dict) -> dict:
    """Fila lista para insertar/actualizar a partir de un dict de entrada."""
    return {
        "h3_index": str(data["h3_index"]),
        "score_general": float(data.get("score_general") or 0.0),
        "tipo_celda": str(data.get("tipo_celda") or "disponible"),
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "variables": _clean_variables(data.get("variables")),
    }


# ── Metadatos (rangos por variable) ───────────────────────────────────────────

def _as_number(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _accumulate_meta(acc: dict[str, list[float]], variables: dict) -> None:
    """Actualiza {variable: [min, max]} con los valores numéricos de una fila."""
    for k, v in (variables or {}).items():
        num = _as_number(v)
        if num is None:
            continue
        cur = acc.get(k)
        if cur is None:
            acc[k] = [num, num]
        else:
            if num < cur[0]: cur[0] = num
            if num > cur[1]: cur[1] = num


def _write_meta(db: Session, acc: dict[str, list[float]]) -> None:
    """Reemplaza `hexagon_meta` con los rangos calculados (tabla diminuta)."""
    db.execute(delete(HexagonMeta))
    if acc:
        db.bulk_insert_mappings(
            HexagonMeta,
            [{"variable": k, "vmin": mn, "vmax": mx} for k, (mn, mx) in acc.items()],
        )


# ── Ingesta destructiva ───────────────────────────────────────────────────────

def copy_ingest(
    db: Session,
    hexagons: Iterable[dict],
    on_progress: Callable[[int], None] | None = None,
    progress_every: int = 50_000,
) -> int:
    """
    Ingesta destructiva de MÁXIMO rendimiento vía **COPY** (Postgres).

    Camino corto `dict → tupla → COPY`: no materializa el dataset, no pasa por el
    ORM ni por `_to_row`, y streamea fila a fila al servidor. Memoria ~constante
    y un orden de magnitud más rápido que `INSERT` para millones de filas.

    ATÓMICA: `TRUNCATE` + `COPY` van en una sola transacción. Si el proceso muere
    a mitad (OOM, kill), hace rollback y la tabla conserva los datos anteriores —
    nunca queda a medias. Esto responde al punto del `count(*)`: se limpia sola.

    `on_progress(total)` se llama cada `progress_every` filas (para logs visibles).
    Solo Postgres (COPY). Para el camino portable, usar `replace_all`.
    """
    now = datetime.now(timezone.utc)
    raw = db.connection().connection.driver_connection  # conexión psycopg subyacente

    total = 0
    meta: dict[str, list[float]] = {}
    with raw.cursor() as cur:
        cur.execute("TRUNCATE TABLE hexagon")
        with cur.copy(f"COPY hexagon ({', '.join(_COPY_COLS)}) FROM STDIN") as cp:
            cp.set_types(["text", "float8", "text", "float8", "float8", "jsonb", "timestamptz"])
            for h in hexagons:
                variables = h.get("variables") or {}
                cp.write_row((
                    str(h["h3_index"]),
                    float(h.get("score_general") or 0.0),
                    str(h.get("tipo_celda") or "disponible"),
                    h.get("lat"),
                    h.get("lng"),
                    Jsonb(variables),
                    now,
                ))
                _accumulate_meta(meta, variables)
                total += 1
                if on_progress and total % progress_every == 0:
                    on_progress(total)

    # `hexagon_meta` en la MISMA transacción (misma conexión) → atómico con el COPY.
    _write_meta(db, meta)
    db.commit()
    if on_progress:
        on_progress(total)
    return total


def replace_all(
    db: Session,
    hexagons: Iterable[dict],
    batch_size: int = 5000,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """
    Reemplazo total en STREAMING vía ORM (portable, no requiere COPY). Borra todo
    e inserta por lotes, confirmando y LIBERANDO memoria en cada lote — pico ~un
    lote, no el dataset. Fallback de `copy_ingest` para entornos sin Postgres/COPY.

    Nota: NO es atómico — confirma por lote, así que durante la recarga hay una
    ventana con la tabla parcialmente poblada.
    """
    db.execute(delete(Hexagon))
    db.commit()  # libera el borrado; a partir de aquí solo crecen lotes acotados

    total = 0
    meta: dict[str, list[float]] = {}
    batch: list[dict] = []
    for h in hexagons:
        row = _to_row(h)
        _accumulate_meta(meta, row["variables"])
        batch.append(row)
        if len(batch) >= batch_size:
            db.bulk_insert_mappings(Hexagon, batch)
            db.commit()
            total += len(batch)
            batch = []  # <- libera el lote
            if on_progress:
                on_progress(total)
    if batch:
        db.bulk_insert_mappings(Hexagon, batch)
        db.commit()
        total += len(batch)

    _write_meta(db, meta)
    db.commit()
    if on_progress:
        on_progress(total)
    return total


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get(db: Session, h3_index: str) -> Hexagon | None:
    return db.get(Hexagon, h3_index)


def list_all(db: Session) -> list[Hexagon]:
    return list(db.scalars(select(Hexagon)))


def count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Hexagon)) or 0


def upsert(db: Session, data: dict) -> Hexagon:
    """Crea el hexágono o actualiza sus campos si ya existe (por `h3_index`)."""
    row = _to_row(data)
    hx = db.get(Hexagon, row["h3_index"])
    if hx is None:
        hx = Hexagon(**row)
        db.add(hx)
    else:
        hx.score_general = row["score_general"]
        hx.tipo_celda = row["tipo_celda"]
        hx.lat = row["lat"]
        hx.lng = row["lng"]
        hx.variables = row["variables"]
    db.commit()
    db.refresh(hx)
    return hx


def update(db: Session, h3_index: str, changes: dict) -> Hexagon | None:
    """Actualiza solo los campos presentes en `changes`. None si no existe."""
    hx = db.get(Hexagon, h3_index)
    if hx is None:
        return None
    if "score_general" in changes:
        hx.score_general = float(changes["score_general"] or 0.0)
    if "tipo_celda" in changes:
        hx.tipo_celda = str(changes["tipo_celda"] or "disponible")
    if "lat" in changes:
        hx.lat = changes["lat"]
    if "lng" in changes:
        hx.lng = changes["lng"]
    if "variables" in changes:
        hx.variables = _clean_variables(changes["variables"])
    db.commit()
    db.refresh(hx)
    return hx


def delete_one(db: Session, h3_index: str) -> bool:
    hx = db.get(Hexagon, h3_index)
    if hx is None:
        return False
    db.delete(hx)
    db.commit()
    return True


# ── Carga para el warmup ──────────────────────────────────────────────────────

def load_for_warmup(db: Session) -> tuple[list[HexagonData], list[str], str]:
    """
    Reconstruye el shape de `read_parquet`: (hexágonos, nombres de variables,
    timestamp). Los nombres de variables se derivan de la unión de claves (el
    índice ya carga todas las filas a memoria, así que el barrido es gratis).
    """
    rows = list_all(db)

    hexagons: list[HexagonData] = []
    variable_names: set[str] = set()
    latest: datetime | None = None

    for r in rows:
        variables = r.variables or {}
        variable_names.update(variables.keys())
        hexagons.append(
            HexagonData(
                h3_index=r.h3_index,
                score_general=r.score_general or 0.0,
                tipo_celda=r.tipo_celda or "disponible",
                variables=variables,
            )
        )
        if r.updated_at and (latest is None or r.updated_at > latest):
            latest = r.updated_at

    parsed_at = (latest or datetime.now(timezone.utc)).isoformat()
    return hexagons, sorted(variable_names), parsed_at
