"""Dependencias de FastAPI: inyección de la fuente de hexágonos (por request)."""
from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from domain.query_engine.source import HexagonSource
from infrastructure.db.session import get_db
from infrastructure.db.hexagon_query_repository import PostgresHexagonSource


def get_hexagon_source(db: Session = Depends(get_db)) -> HexagonSource:
    """El servicio depende de la abstracción; aquí se resuelve a la impl Postgres."""
    return PostgresHexagonSource(db)
