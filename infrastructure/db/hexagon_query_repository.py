"""
Implementación Postgres del puerto `HexagonSource` (domain/query_engine/source).

Consulta la tabla `hexagon` POR REQUEST — nada se carga a memoria al arrancar.
- área/detalle → `WHERE h3_index = ANY(:celdas)` / `= :h` (usan la PK: O(log n)).
- tiles        → `WHERE lat BETWEEN … AND lng BETWEEN …` (btree en lat/lng).
- variables/rangos → tabla `hexagon_meta` (diminuta, precalculada en la ingesta).

Se instancia por request con la sesión de FastAPI (`Depends(get_db)`).
"""
from __future__ import annotations
from typing import Optional

from sqlalchemy import func, select, text
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


def _to_row(r) -> HexRow:
    return HexRow(
        h3_index=r[0],
        score_general=r[1] or 0.0,
        tipo_celda=r[2] or "disponible",
        lat=r[3] if r[3] is not None else 0.0,
        lng=r[4] if r[4] is not None else 0.0,
        variables=r[5] or {},
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
    def fetch_bbox(self, west: float, south: float, east: float, north: float) -> list[HexRow]:
        stmt = select(*_COLS).where(
            Hexagon.lat >= south, Hexagon.lat <= north,
            Hexagon.lng >= west,  Hexagon.lng <= east,
        )
        return [_to_row(r) for r in self.db.execute(stmt)]

    def stream_bbox(self, west: float, south: float, east: float, north: float):
        """Como `fetch_bbox` pero STREAMING (cursor de servidor + `yield_per`): NO
        materializa todo el bbox en RAM. Para agregar la capa de una variable sobre
        zonas grandes sin reventar la memoria (causa del OOM en `/tiles/variable`).
        Cada lote se procesa y se libera; el llamador solo retiene el agregado."""
        stmt = (
            select(*_COLS)
            .where(
                Hexagon.lat >= south, Hexagon.lat <= north,
                Hexagon.lng >= west,  Hexagon.lng <= east,
            )
            # lote pequeño: cada HexRow trae el JSONB `variables` completo; en un
            # server chico (~2 GB) un yield_per grande hincha el pico por request.
            .execution_options(stream_results=True, yield_per=5_000)
        )
        for r in self.db.execute(stmt):
            yield _to_row(r)

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

    # ── Estado ───────────────────────────────────────────────────────────────
    def count(self) -> int:
        return self.db.scalar(select(func.count()).select_from(Hexagon)) or 0

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
