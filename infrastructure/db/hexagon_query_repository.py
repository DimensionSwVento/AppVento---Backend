"""
Implementación Postgres del puerto `HexagonSource` (domain/query_engine/source).

Consulta la tabla `hexagon` POR REQUEST — nada se carga a memoria al arrancar.
- área/detalle → `WHERE h3_index = ANY(:celdas)` / `= :h` (usan la PK: O(log n)).
- tiles        → `WHERE geom && ST_MakeEnvelope(...)` (índice espacial GiST
  sobre `geom`, PostGIS — migración 0017; antes rango btree en lat/lng).
- variables/rangos → tabla `hexagon_meta` (diminuta, precalculada en la ingesta).

Se instancia por request con la sesión de FastAPI (`Depends(get_db)`).
"""
from __future__ import annotations
from typing import Optional

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from domain.query_engine.source import HexRow
from infrastructure.db.models import Hexagon, HexagonMeta

# Solo las columnas necesarias (filas ligeras, no objetos ORM completos).
_COLS = (
    Hexagon.h3_index,
    Hexagon.score_general,
    Hexagon.tipo_celda,
    Hexagon.lat,
    Hexagon.lng,
    Hexagon.variables,
)
# Fase C2 (liviana): igual que `_COLS` pero SIN `variables` — el campo más
# pesado de la fila (JSONB con decenas de claves). `get_tile` (la cuadrícula
# base, la consulta de MAYOR frecuencia: cada pan/zoom, para TODOS los tiles
# visibles) no necesita `variables` salvo que pida una variable puntual (`var`)
# o haya variable de mar/tierra configurada (`OFFSHORE_VAR`) — en el caso común
# (ninguna de las dos) evita traer el JSONB para cada fila del bbox.
_COLS_LIGHT = (
    Hexagon.h3_index,
    Hexagon.score_general,
    Hexagon.tipo_celda,
    Hexagon.lat,
    Hexagon.lng,
)


def _to_row(r) -> HexRow:
    # `r` trae 6 columnas (`_COLS`, con variables) o 5 (`_COLS_LIGHT`, sin
    # variables) según qué constante se usó en el SELECT que lo originó.
    has_variables = len(r) > 5
    return HexRow(
        h3_index=r[0],
        score_general=r[1] or 0.0,
        tipo_celda=r[2] or "disponible",
        lat=r[3] if r[3] is not None else 0.0,
        lng=r[4] if r[4] is not None else 0.0,
        variables=(r[5] or {}) if has_variables else {},
    )


class PostgresHexagonSource:
    """Fuente de hexágonos respaldada por Postgres (sin estado en RAM)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Por celda ────────────────────────────────────────────────────────────
    def fetch_cells(self, cells: list[str]) -> list[HexRow]:
        if not cells:
            return []
        stmt = select(*_COLS).where(Hexagon.h3_index.in_(cells))
        return [_to_row(r) for r in self.db.execute(stmt)]

    def stream_cells(self, cells: list[str]):
        """Como `fetch_cells` pero STREAMING (cursor de servidor + `yield_per`): NO
        materializa todas las celdas del área en RAM. Para el informe sobre áreas
        grandes — cada `HexRow` trae el JSONB `variables` completo, así que una
        selección amplia hincharía el pico por request (misma causa que el OOM del
        bbox). Cada lote se procesa y se libera; el llamador solo retiene su salida."""
        if not cells:
            return
        stmt = (
            select(*_COLS)
            .where(Hexagon.h3_index.in_(cells))
            .execution_options(stream_results=True, yield_per=5_000)
        )
        for r in self.db.execute(stmt):
            yield _to_row(r)

    def fetch_one(self, h3_index: str) -> Optional[HexRow]:
        stmt = select(*_COLS).where(Hexagon.h3_index == h3_index)
        r = self.db.execute(stmt).first()
        return _to_row(r) if r else None

    def fetch_variables_pon(self, cells: list[str]) -> dict[str, dict]:
        """Valores PONDERADOS por hex (tabla `hexagon_variables_pon`) para las
        celdas dadas → {h3_index: {code: valor}}. Vacío si no hay ponderadas."""
        if not cells:
            return {}
        from infrastructure.db.models import HexagonVariablesPon  # noqa: PLC0415
        stmt = select(
            HexagonVariablesPon.h3_index, HexagonVariablesPon.variables_pon
        ).where(HexagonVariablesPon.h3_index.in_(cells))
        return {h3: (vp or {}) for h3, vp in self.db.execute(stmt)}

    # ── Por bbox (tiles) ─────────────────────────────────────────────────────
    # Filtro ESPACIAL (PostGIS, migración 0017): `geom && ST_MakeEnvelope(...)`
    # usa el índice GiST `idx_hexagon_geom` (bounding-box overlap, O(log n) real)
    # en vez del rango btree sobre `lat`/`lng`. `lat`/`lng` siguen en `_COLS`
    # (se devuelven en la fila), solo cambia la condición del WHERE.
    _BBOX_PREDICATE = text(
        "geom && ST_MakeEnvelope(:west, :south, :east, :north, 4326)"
    )

    def fetch_bbox(
        self, west: float, south: float, east: float, north: float,
        include_variables: bool = True,
    ) -> list[HexRow]:
        cols = _COLS if include_variables else _COLS_LIGHT
        stmt = (
            select(*cols)
            .where(self._BBOX_PREDICATE)
            .params(west=west, south=south, east=east, north=north)
        )
        return [_to_row(r) for r in self.db.execute(stmt)]

    def stream_bbox(
        self, west: float, south: float, east: float, north: float,
        include_variables: bool = True,
    ):
        """Como `fetch_bbox` pero STREAMING (cursor de servidor + `yield_per`): NO
        materializa todo el bbox en RAM. Para agregar la capa de una variable sobre
        zonas grandes sin reventar la memoria (causa del OOM en `/tiles/variable`).
        Cada lote se procesa y se libera; el llamador solo retiene el agregado.

        `include_variables=False` (Fase C2): omite el JSONB `variables` cuando el
        llamador no lo necesita — la cuadrícula base (`get_tile`) no lo usa salvo
        variable puntual o mar/tierra configurado, y es la consulta de mayor
        frecuencia (cada pan/zoom, para todos los tiles visibles)."""
        cols = _COLS if include_variables else _COLS_LIGHT
        stmt = (
            select(*cols)
            .where(self._BBOX_PREDICATE)
            .params(west=west, south=south, east=east, north=north)
            # lote pequeño: cada HexRow trae el JSONB `variables` completo; en un
            # server chico (~2 GB) un yield_per grande hincha el pico por request.
            .execution_options(stream_results=True, yield_per=5_000)
        )
        for r in self.db.execute(stmt):
            yield _to_row(r)

    # ── Vistas materializadas por resolución (Fase C5) ───────────────────────
    # Nombre de tabla fijo (nunca viene de input externo) → interpolar en el SQL
    # es seguro (no hay forma de inyectar vía `res`, que solo puede ser 5 o 6
    # para pegar aquí; cualquier otro valor cae al `None` de abajo).
    _RES_TO_MV = {5: "mv_hexagon_res5", 6: "mv_hexagon_res6"}

    def fetch_resolution_mv(
        self, res: int, west: float, south: float, east: float, north: float,
    ) -> Optional[list[HexRow]]:
        mv = self._RES_TO_MV.get(res)
        if mv is None:
            return None  # sin vista precomputada para esta resolución
        # Pocas filas (miles, no millones) → un scan con filtro lat/lng alcanza;
        # no hace falta geom/GiST en la vista (ver migración 0019).
        stmt = text(
            f"SELECT h3_index, score_general, is_excluded, lat, lng, variables "
            f"FROM {mv} WHERE lat BETWEEN :south AND :north "
            f"AND lng BETWEEN :west AND :east"
        ).bindparams(west=west, south=south, east=east, north=north)
        out: list[HexRow] = []
        for h3_index, score_general, is_excluded, lat, lng, variables in self.db.execute(stmt):
            out.append(HexRow(
                # La MV guarda `h3_index` como BIGINT (deriva de h3_res5/6). El
                # `text()` crudo NO pasa por el TypeDecorator `H3Index`, así que hay
                # que convertir el entero al TOKEN hex igual que `process_result_value`
                # (`format(int, "x")`). Sin esto, el front recibe un número y
                # `cellToBoundary` falla → la capa NO se dibuja (res5/6 en blanco,
                # mientras res7/8 por el ORM sí convertían).
                h3_index=format(int(h3_index), "x") if h3_index is not None else "",
                score_general=score_general or 0.0,
                tipo_celda="excluida" if is_excluded else "disponible",
                lat=lat if lat is not None else 0.0,
                lng=lng if lng is not None else 0.0,
                variables=variables or {},
            ))
        return out

    # ── Excluidos (capa completa, una sola petición) ─────────────────────────
    def excluded_cells(self) -> list[str]:
        """SOLO los h3_index de zonas EXCLUIDAS (`tipo_celda='excluida'` o el
        `global` de la master < 0). Payload ligero (solo strings) → el front lo
        pide UNA vez y lo cachea, en vez de escanear tile-por-tile con `get_tile`
        (que a zoom bajo barría millones de filas por cada tile)."""
        stmt = select(Hexagon.h3_index).where(
            or_(Hexagon.tipo_celda == "excluida", Hexagon.score_general < 0)
        )
        return list(self.db.scalars(stmt))

    # ── Metadatos ────────────────────────────────────────────────────────────
    def variable_names(self) -> list[str]:
        return list(self.db.scalars(select(HexagonMeta.variable).order_by(HexagonMeta.variable)))

    def variable_range(self, var_name: str) -> Optional[dict[str, float]]:
        r = self.db.execute(
            select(HexagonMeta.vmin, HexagonMeta.vmax).where(HexagonMeta.variable == var_name)
        ).first()
        return {"min": r[0], "max": r[1]} if r else None

    def variable_ranges(self) -> dict[str, dict[str, float]]:
        return {
            v: {"min": mn, "max": mx}
            for v, mn, mx in self.db.execute(
                select(HexagonMeta.variable, HexagonMeta.vmin, HexagonMeta.vmax)
            )
        }

    def variable_quantiles(self, var_name: str, bands: int = 5) -> Optional[dict]:
        """Cortes de CUANTIL (bands-1 cortes) + min/max de `var_name` sobre TODA la
        tabla, para la ESCALA DE COLOR choropleth (bandas claro→oscuro por cuantiles,
        pedido del cliente). `percentile_cont` interpola; solo cuenta valores
        numéricos del JSONB `variables`. `bands` lo controla el server (int acotado),
        por eso las fracciones se interpolan en el SQL sin riesgo; `var_name` va por
        bind. Devuelve None si la variable no tiene valores numéricos."""
        bands = max(2, min(12, int(bands)))
        fracs = ", ".join(f"{i / bands:.6f}" for i in range(1, bands))
        row = self.db.execute(text(
            f"SELECT percentile_cont(ARRAY[{fracs}]) "
            "WITHIN GROUP (ORDER BY (variables ->> :v)::float8) AS cuts, "
            "min((variables ->> :v)::float8) AS vmin, "
            "max((variables ->> :v)::float8) AS vmax "
            "FROM hexagon WHERE jsonb_typeof(variables -> :v) = 'number'"
        ).bindparams(v=var_name)).first()
        if not row or row[0] is None:
            return None
        return {
            "cuts": [float(c) for c in row[0]],
            "min": float(row[1]) if row[1] is not None else 0.0,
            "max": float(row[2]) if row[2] is not None else 0.0,
            "bands": bands,
        }

    # ── Estado ───────────────────────────────────────────────────────────────
    def count(self) -> int:
        return self.db.scalar(select(func.count()).select_from(Hexagon)) or 0

    def count_estimate(self) -> int:
        """Conteo APROXIMADO e instantáneo del planner (`pg_class.reltuples`), sin
        escanear la tabla. El `count(*)` exacto sobre ~1,6 M filas costaba ~720 ms
        (index-only scan con cientos de miles de heap-fetches) y es lo que tumbaba
        `/tiles/warmup` bajo carga. Se usa para el bundle de metadatos (número
        informativo); si las estadísticas aún no se poblaron (reltuples ≤ 0, tabla
        recién ingerida sin ANALYZE) cae al conteo exacto una vez."""
        r = self.db.execute(
            text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'hexagon'")
        ).scalar()
        return int(r) if r and r > 0 else self.count()

    def counts(self, selected: list[str], var_name: Optional[str] = None,
               threshold: float = 0.33) -> dict:
        total = self.count()

        selected_n = 0
        if selected:
            selected_n = self.db.scalar(
                select(func.count()).select_from(Hexagon).where(Hexagon.h3_index.in_(selected))
            ) or 0

        rng = self.variable_range(var_name) if var_name else None
        if var_name and rng:
            # variable normalizada > threshold  ⇔  raw > min + threshold*(max-min)
            cutoff = rng["min"] + threshold * (rng["max"] - rng["min"])
            above = self.db.scalar(text(
                "SELECT count(*) FROM hexagon "
                "WHERE jsonb_typeof(variables -> :v) = 'number' "
                "AND (variables ->> :v)::float8 > :cut"
            ).bindparams(v=var_name, cut=cutoff)) or 0
        else:
            above = self.db.scalar(
                select(func.count()).select_from(Hexagon).where(Hexagon.score_general > threshold)
            ) or 0

        return {"total": total, "selected": selected_n, "aboveThreshold": above}

    def is_ready(self) -> bool:
        # Existencia O(1) por PK (no cuenta todo el dataset en cada request).
        try:
            return self.db.execute(select(Hexagon.h3_index).limit(1)).first() is not None
        except Exception:
            return False
