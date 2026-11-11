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

import h3
from psycopg.types.json import Jsonb
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from domain.models.hexagon import HexagonData
from infrastructure.db.models import Hexagon, HexagonMeta

# Orden de columnas para la ingesta masiva (COPY / bulk). `updated_at` se fija a un
# único timestamp por corrida.
# Escalera de resoluciones con vista materializada de resumen. Cada `res{N}` tiene
# una columna PADRE `h3_res{N}` (BIGINT, misma convención que `h3_index`) y una MV
# `mv_hexagon_res{N}` que agrega por GROUP BY — sin esto, resumir por resolución
# exigiría recalcular el padre en cada consulta. res2–4 = escalones de ALEJAR
# (presupuesto N chico); res5/6 = los originales. res7/8 van por stream_bbox (bbox
# chico). UNA sola fuente de verdad para no repetir la lista en ingesta/MVs/query.
_MV_RES: tuple[int, ...] = (2, 3, 4, 5, 6, 7)
_H3_RES_COLS: tuple[str, ...] = tuple(f"h3_res{r}" for r in _MV_RES)
_MV_NAME = {r: f"mv_hexagon_res{r}" for r in _MV_RES}

_COPY_COLS = (
    "h3_index", "score_general", "tipo_celda", "lat", "lng", "variables",
    *_H3_RES_COLS, "updated_at",
)


def _h3_parent_bigint(token: str, res: int) -> int:
    """Celda PADRE de `token` (hex H3) a resolución `res`, como BIGINT — misma
    conversión que usa `H3Index` para `h3_index`. No depende de `h3-pg` (no
    verificable/instalable en todos los entornos): se calcula en Python con la
    librería `h3` ya usada en el resto del backend (`cell_to_parent`,
    `aggregator.py`/`tile_service.py`)."""
    return int(h3.cell_to_parent(str(token), res), 16)


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
    h3_index = str(data["h3_index"])
    return {
        "h3_index": h3_index,
        "score_general": float(data.get("score_general") or 0.0),
        "tipo_celda": str(data.get("tipo_celda") or "disponible"),
        "lat": data.get("lat"),
        "lng": data.get("lng"),
        "variables": _clean_variables(data.get("variables")),
        # Bundle del modelo: scores precalculados por criterio/subcriterio +
        # emplazamiento de origen (onshore/offshore). Vacíos por defecto → la
        # ingesta clásica de un solo parquet sigue funcionando igual.
        "scores": _clean_variables(data.get("scores")),
        "emplazamiento": str(data.get("emplazamiento") or "onshore"),
        # Fase C1: celda padre en cada resolución de la escalera (BIGINT) — agregación
        # por resolución (ver `_h3_parent_bigint`).
        **{f"h3_res{r}": _h3_parent_bigint(h3_index, r) for r in _MV_RES},
        # Explícito: los caminos Core/text (upsert/blue-green) no aplican el
        # default Python del ORM, así que fijamos el timestamp de la corrida.
        "updated_at": datetime.now(timezone.utc),
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


# ── Vistas materializadas por resolución (Fase C3/C4) ─────────────────────────

def refresh_resolution_mvs(db: Session) -> None:
    """Refresca `mv_hexagon_res5`/`mv_hexagon_res6` (migración 0019) tras una
    ingesta que escribe sobre la MISMA tabla `hexagon` (mismo OID todo el
    tiempo: `copy_ingest` hace TRUNCATE+COPY, `replace_all`/`upsert_all` hacen
    DELETE/INSERT — nunca recrean la tabla). CONCURRENTLY: no bloquea lecturas
    de la vista mientras se recalcula; costo de FONDO (una vez por ingesta), no
    de request. Para el camino por paquetes, se llama una sola vez en
    `bundle_service.finalize` (no por cada chunk).

    NO usar esta función tras `blue_green_replace` — ver `recreate_resolution_mvs`."""
    for mv in _MV_NAME.values():
        db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
    db.commit()


def _resolution_mv_select(res_col: str) -> str:
    """Cuerpo del SELECT de `mv_hexagon_res5`/`res6` (debe coincidir con el de
    la migración 0019 — ver esa migración para la explicación completa del
    agregado). Duplicado intencional: las migraciones de Alembic deben quedar
    AUTOCONTENIDAS (no importar código de la app que puede evolucionar después
    y romper la reproducibilidad de una migración vieja); este módulo necesita
    la MISMA definición para `recreate_resolution_mvs`."""
    return f"""
        SELECT
          base.parent AS h3_index,
          base.score_general,
          base.is_excluded,
          base.lat,
          base.lng,
          COALESCE(agg_vars.variables, '{{}}'::jsonb) AS variables
        FROM (
          SELECT
            {res_col} AS parent,
            AVG(score_general)::real AS score_general,
            BOOL_OR(tipo_celda = 'excluida' OR score_general < 0) AS is_excluded,
            AVG(lat)::real AS lat,
            AVG(lng)::real AS lng
          FROM hexagon
          WHERE {res_col} IS NOT NULL
          GROUP BY {res_col}
        ) AS base
        LEFT JOIN (
          SELECT parent, jsonb_object_agg(key, avg_value) AS variables
          FROM (
            SELECT
              h.{res_col} AS parent,
              kv.key,
              AVG(kv.value::double precision) AS avg_value
            FROM hexagon h
            CROSS JOIN LATERAL jsonb_each_text(h.variables) AS kv(key, value)
            WHERE h.{res_col} IS NOT NULL
              AND kv.value ~ '^-?[0-9]+(\\.[0-9]+)?$'
            GROUP BY h.{res_col}, kv.key
          ) AS per_key
          GROUP BY parent
        ) AS agg_vars ON agg_vars.parent = base.parent
    """


def recreate_resolution_mvs(db: Session) -> None:
    """DROP + CREATE de `mv_hexagon_res5`/`res6` (no un simple REFRESH).

    Por qué: `blue_green_replace` NO actualiza la tabla `hexagon` existente —
    crea una tabla NUEVA (`hexagon_new`, otro OID) y la pone en el lugar de
    `hexagon` por RENAME. Postgres resuelve las vistas materializadas por el
    OID de la relación referenciada al momento de su creación (igual que una
    vista normal: sobrevive al rename de esa tabla, pero NO se re-apunta sola
    si otra tabla distinta toma luego ese mismo nombre). Si solo se hiciera
    REFRESH tras el swap, la vista seguiría leyendo la tabla VIEJA (ahora
    `hexagon_backup`) en silencio — datos desactualizados sin ningún error.
    Recrear la vista después del swap la reata a la tabla `hexagon` ACTUAL."""
    for r, mv in _MV_NAME.items():
        res_col = f"h3_res{r}"
        db.execute(text(f"DROP MATERIALIZED VIEW IF EXISTS {mv}"))
        db.execute(text(f"CREATE MATERIALIZED VIEW {mv} AS{_resolution_mv_select(res_col)}"))
        db.execute(text(f"CREATE UNIQUE INDEX idx_{mv}_pk ON {mv} (h3_index)"))
    db.commit()


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
            # h3_index/h3_res5/h3_res6 van como int8 (BIGINT): el entero nativo
            # del índice H3 (y de sus padres, Fase C1). El COPY no pasa por el
            # ORM, así que convertimos el token hex a mano (misma conversión
            # que `_h3_parent_bigint`/`H3Index`).
            cp.set_types([
                "int8", "float8", "text", "float8", "float8", "jsonb",
                *(["int8"] * len(_MV_RES)), "timestamptz",
            ])
            for h in hexagons:
                variables = h.get("variables") or {}
                token = str(h["h3_index"])
                cp.write_row((
                    int(token, 16),
                    float(h.get("score_general") or 0.0),
                    str(h.get("tipo_celda") or "disponible"),
                    h.get("lat"),
                    h.get("lng"),
                    Jsonb(variables),
                    *(_h3_parent_bigint(token, r) for r in _MV_RES),
                    now,
                ))
                _accumulate_meta(meta, variables)
                total += 1
                if on_progress and total % progress_every == 0:
                    on_progress(total)

    # `hexagon_meta` en la MISMA transacción (misma conexión) → atómico con el COPY.
    _write_meta(db, meta)
    db.commit()
    refresh_resolution_mvs(db)
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
    refresh_resolution_mvs(db)
    if on_progress:
        on_progress(total)
    return total


# ── Ingesta SEGURA (no frena la generación de informes) ───────────────────────

def _upsert_batch(db: Session, batch: list[dict]) -> None:
    """UPSERT por `h3_index` (INSERT … ON CONFLICT DO UPDATE). Postgres."""
    if not batch:
        return
    stmt = pg_insert(Hexagon.__table__).values(batch)
    cols = [k for k in batch[0].keys() if k != "h3_index"]
    set_ = {c: stmt.excluded[c] for c in cols}
    set_["updated_at"] = func.now()
    db.execute(stmt.on_conflict_do_update(index_elements=["h3_index"], set_=set_))


# ── Ingesta POR PAQUETES (modular: actualiza solo las columnas conciliadas) ────

def _upsert_chunk(db: Session, batch: list[dict], provided: set[str]) -> None:
    """UPSERT de un paquete (row-group de un parquet ya conciliado).

    A diferencia de `_upsert_batch` (que reemplaza toda la fila), en el CONFLICTO
    solo actualiza las columnas que ESTE parquet aporta (`provided`) y **fusiona**
    los JSONB (`variables`/`scores` con `||`) en vez de reemplazarlos → respeta la
    modularidad: subir una columna actualiza solo esa clave y deja el resto quieto.
    El INSERT (hex nuevo) trae todas las columnas con sus defaults.
    """
    if not batch:
        return
    c = Hexagon.__table__.c
    stmt = pg_insert(Hexagon.__table__).values(batch)
    set_: dict = {}
    if "variables" in provided:
        set_["variables"] = func.coalesce(c.variables, text("'{}'::jsonb")).op("||")(stmt.excluded.variables)
    if "scores" in provided:
        set_["scores"] = func.coalesce(c.scores, text("'{}'::jsonb")).op("||")(stmt.excluded.scores)
    if "tipo_celda" in provided:
        set_["tipo_celda"] = stmt.excluded.tipo_celda
    if "score_general" in provided:
        set_["score_general"] = stmt.excluded.score_general
    # lat/lng/emplazamiento/h3_res{N} son consistentes por hex (derivan de
    # h3_index, que no cambia) → se refrescan siempre, sin importar `provided`.
    set_["lat"] = stmt.excluded.lat
    set_["lng"] = stmt.excluded.lng
    set_["emplazamiento"] = stmt.excluded.emplazamiento
    for col in _H3_RES_COLS:
        set_[col] = getattr(stmt.excluded, col)
    set_["updated_at"] = func.now()
    db.execute(stmt.on_conflict_do_update(index_elements=["h3_index"], set_=set_))


def upsert_chunk(db: Session, hexagons: Iterable[dict], provided: set[str],
                 batch_size: int = 5000) -> int:
    """Upsertea un paquete de hex fusionando solo las columnas `provided`."""
    total = 0
    batch: list[dict] = []
    for h in hexagons:
        batch.append(_to_row(h))
        if len(batch) >= batch_size:
            _upsert_chunk(db, batch, provided)
            db.commit()
            total += len(batch)
            batch = []
    if batch:
        _upsert_chunk(db, batch, provided)
        db.commit()
        total += len(batch)
    return total


def merge_meta(db: Session, acc: dict[str, list[float]]) -> None:
    """Acumula rangos en `hexagon_meta` SIN borrar (upsert min/max). Para la
    ingesta por paquetes: cada lote refina vmin/vmax de SUS variables sin tocar
    las demás (a diferencia de `_write_meta`, que reemplaza toda la tabla)."""
    mc = HexagonMeta.__table__.c
    for k, (mn, mx) in acc.items():
        stmt = pg_insert(HexagonMeta.__table__).values(variable=k, vmin=mn, vmax=mx)
        stmt = stmt.on_conflict_do_update(
            index_elements=["variable"],
            set_={
                "vmin": func.least(mc.vmin, stmt.excluded.vmin),
                "vmax": func.greatest(mc.vmax, stmt.excluded.vmax),
            },
        )
        db.execute(stmt)
    db.commit()


def upsert_all(
    db: Session,
    hexagons: Iterable[dict],
    batch_size: int = 5000,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """
    Ingesta NO destructiva (lo que dispara la INTERFAZ): actualiza los hex
    existentes y añade los nuevos por lotes, SIN borrar. Así subir millones de
    filas no deja la tabla vacía ni frena los informes. `hexagon_meta` se
    reemplaza al final con los rangos vistos en esta corrida.
    """
    total = 0
    meta: dict[str, list[float]] = {}
    batch: list[dict] = []
    for h in hexagons:
        row = _to_row(h)
        _accumulate_meta(meta, row["variables"])
        batch.append(row)
        if len(batch) >= batch_size:
            _upsert_batch(db, batch)
            db.commit()
            total += len(batch)
            batch = []
            if on_progress:
                on_progress(total)
    if batch:
        _upsert_batch(db, batch)
        db.commit()
        total += len(batch)
    _write_meta(db, meta)
    db.commit()
    refresh_resolution_mvs(db)
    if on_progress:
        on_progress(total)
    return total


def blue_green_replace(
    db: Session,
    hexagons: Iterable[dict],
    batch_size: int = 5000,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """
    Ingesta DESTRUCTIVA SEGURA (lo que corre por CONSOLA en prod): carga TODO en
    una tabla nueva `hexagon_new` y al final hace un SWAP atómico por rename,
    dejando la anterior como BACKUP (`hexagon_backup`). La app lee `hexagon`
    durante toda la carga (nunca ve datos a medio cargar ni se frena). Postgres.
    """
    db.execute(text("DROP TABLE IF EXISTS hexagon_new"))
    db.execute(text("CREATE TABLE hexagon_new (LIKE hexagon INCLUDING ALL)"))
    # `LIKE ... INCLUDING ALL` copia índices/constraints/defaults pero NUNCA
    # triggers (ni con INCLUDING ALL — limitación de Postgres). Sin esto,
    # `hexagon_new` perdería el trigger `trg_hexagon_set_geom` (migración
    # 0017) y, tras el swap, los hex ingeridos por este camino quedarían con
    # `geom` NULL (rompe el filtro espacial por bbox de forma silenciosa). La
    # función `hexagon_set_geom()` ya existe (creada en esa migración); solo
    # hace falta re-adjuntar el trigger a esta instancia de tabla. Una vez
    # adjunto, SÍ sobrevive al RENAME del swap de abajo.
    db.execute(text(
        "CREATE TRIGGER trg_hexagon_set_geom "
        "BEFORE INSERT OR UPDATE OF lat, lng ON hexagon_new "
        "FOR EACH ROW EXECUTE FUNCTION hexagon_set_geom()"
    ))
    db.commit()

    total = 0
    meta: dict[str, list[float]] = {}
    batch: list[dict] = []

    def flush() -> None:
        nonlocal total, batch
        if not batch:
            return
        # INSERT directo en `hexagon_new` (mismo shape de columnas que Hexagon).
        keys = list(batch[0].keys())
        cols = ", ".join(keys)
        placeholders = ", ".join(f":{k}" for k in keys)
        db.execute(text(f"INSERT INTO hexagon_new ({cols}) VALUES ({placeholders})"),
                   [_pg_row(r) for r in batch])
        db.commit()
        total += len(batch)
        batch = []
        if on_progress:
            on_progress(total)

    for h in hexagons:
        row = _to_row(h)
        _accumulate_meta(meta, row["variables"])
        batch.append(row)
        if len(batch) >= batch_size:
            flush()
    flush()

    # SWAP atómico: hexagon → backup, hexagon_new → hexagon.
    db.execute(text("DROP TABLE IF EXISTS hexagon_backup CASCADE"))
    db.execute(text("ALTER TABLE hexagon RENAME TO hexagon_backup"))
    db.execute(text("ALTER TABLE hexagon_new RENAME TO hexagon"))
    db.commit()

    _write_meta(db, meta)
    db.commit()
    # NO `refresh_resolution_mvs`: el swap creó una tabla NUEVA (otro OID) — hay
    # que RECREAR las vistas para que se aten a la `hexagon` actual (ver
    # `recreate_resolution_mvs`), no solo refrescarlas.
    recreate_resolution_mvs(db)
    if on_progress:
        on_progress(total)
    return total


def _pg_row(row: dict) -> dict:
    """Adapta un `_to_row` para el INSERT textual: dicts JSONB → Jsonb() y el
    token hex de `h3_index` → int (la columna es BIGINT; el INSERT por `text()` no
    pasa por el ORM, así que convertimos a mano)."""
    out = dict(row)
    for k in ("variables", "scores"):
        if k in out and out[k] is not None:
            out[k] = Jsonb(out[k])
    if out.get("h3_index") is not None and not isinstance(out["h3_index"], int):
        out["h3_index"] = int(str(out["h3_index"]), 16)
    return out


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
