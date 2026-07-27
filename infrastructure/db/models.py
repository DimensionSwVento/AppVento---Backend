"""Modelos ORM (Postgres): usuario local (espejo de Keycloak), informe guardado
e hexágono (espejo persistido del `.parquet`, fuente de runtime del mapa)."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AppUser(Base):
    """
    Espejo local del usuario de Keycloak (fuente de verdad = Keycloak).
    Se provisiona/actualiza en cada request autenticado (ver keycloak_guard).
    """
    __tablename__ = "app_user"

    sub: Mapped[str] = mapped_column(String, primary_key=True)  # `sub` del JWT
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Informe(Base):
    """
    Informe de prefactibilidad guardado por un usuario. Se persisten los
    PARÁMETROS compactados (no el PDF); el informe se regenera al reabrirlo.
    """
    __tablename__ = "informe"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.sub", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    nombre: Mapped[str] = mapped_column(String)
    hexes: Mapped[list] = mapped_column(JSONB, default=list)
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    caracteristicas: Mapped[dict] = mapped_column(JSONB, default=dict)
    scores_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    area_km2: Mapped[float] = mapped_column(Float, default=0.0)


class Hexagon(Base):
    """
    Espejo persistido de una fila del `.h3.parquet`. Es la FUENTE DE RUNTIME del
    mapa: el `warmup` carga esta tabla al índice H3 en memoria (antes leía el
    parquet directo). El parquet queda como ENTRADA DE INGESTA, no como runtime.

    Las variables del modelo (`REG_034`, `TEC_001_sv001`, `SOE_001`, …) son
    dinámicas, dispersas y heterogéneas (str | float | None); se guardan en un
    único `variables` JSONB para no acoplar el esquema al set de columnas del
    parquet (que cambia cuando llega el dataset definitivo).

    Mantenimiento: `hexagon_repository.replace_all` (ingesta DESTRUCTIVA, para las
    muestras) y las operaciones CRUD (para mantenerla al día luego).
    """
    __tablename__ = "hexagon"

    h3_index: Mapped[str] = mapped_column(String, primary_key=True)
    score_general: Mapped[float] = mapped_column(Float, default=0.0)
    tipo_celda: Mapped[str] = mapped_column(String, default="disponible")
    # lat/lng nullables: el índice en memoria los deriva del h3_index si faltan.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    variables: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
