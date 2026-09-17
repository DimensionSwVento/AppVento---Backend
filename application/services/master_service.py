"""
Servicio de actualización de la MASTER (dataset de hexágonos).

- El admin puede lanzar la actualización **inmediata** o **programada** a una
  hora (desde el frontend). Advertencia: durante la ingesta los informes pueden
  salir inconsistentes.
- Cada versión queda registrada (`MasterVersion`) con su estado y momento de
  aplicación; los informes se estampan con la versión vigente (trazabilidad).

La ingesta corre en un hilo aparte (con su propia sesión) para no bloquear las
requests. No hay scheduler externo: un tick de fondo (lifespan) ejecuta las
programadas cuando llega su hora.
"""
from __future__ import annotations
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from application.services import tile_service
from infrastructure.db import master_repository as repo
from infrastructure.db.models import MasterVersion

log = logging.getLogger("vento")

_WARNING = (
    "Actualizar la master puede dejar INCONSISTENTES los informes generados "
    "mientras dura la ingesta. Prográmala en baja demanda si es posible."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dict(mv: MasterVersion | None) -> dict | None:
    if mv is None:
        return None
    return {
        "id": mv.id,
        "version": mv.version,
        "status": mv.status,
        "source": mv.source,
        "scheduledAt": mv.scheduled_at.isoformat() if mv.scheduled_at else None,
        "appliedAt": mv.applied_at.isoformat() if mv.applied_at else None,
        "hexCount": mv.hex_count,
        "varCount": mv.var_count,
        "notes": mv.notes,
        "createdAt": mv.created_at.isoformat(),
    }


def status(db: Session) -> dict:
    """Estado para el frontend: versión vigente, si hay ingesta en curso, y la
    próxima programada. Incluye la advertencia a mostrar en la UI."""
    return {
        "current": _to_dict(repo.current(db)),
        "updating": repo.is_updating(db),
        "scheduled": _to_dict(repo.latest_scheduled(db)),
        "warning": _WARNING,
    }


def current_version_label(db: Session) -> str | None:
    """Etiqueta de la versión vigente (para estampar en los informes)."""
    cur = repo.current(db)
    return cur.version if cur else None


def request_update(db: Session, mode: str, scheduled_at: str | None,
                   source: str | None) -> dict:
    """
    Lanza (inmediata) o programa una actualización de la master.
    `mode`: "immediate" | "scheduled". Devuelve la versión creada.
    """
    if repo.is_updating(db):
        raise ValueError("Ya hay una actualización de la master en curso.")

    if mode == "scheduled":
        when = _parse_dt(scheduled_at)
        if when is None:
            raise ValueError("Falta 'scheduledAt' válido para la programada.")
        mv = repo.create(db, {
            "version": _label(when),
            "status": "scheduled",
            "source": source,
            "scheduled_at": when,
        })
        return _to_dict(mv)

    # Inmediata: se registra 'updating' y se ejecuta en background.
    mv = repo.create(db, {
        "version": _label(_now()),
        "status": "updating",
        "source": source,
    })
    _start_ingest(mv.id, source)
    return _to_dict(mv)


def run_due(db: Session) -> None:
    """Tick de fondo: ejecuta las programadas cuya hora ya llegó (una a la vez)."""
    if repo.is_updating(db):
        return
    for mv in repo.due_scheduled(db, _now()):
        repo.update(db, mv.id, {"status": "updating"})
        _start_ingest(mv.id, mv.source)
        break  # una por tick


# ── Ejecución de la ingesta (hilo aparte, sesión propia) ──────────────────────

def _start_ingest(version_id: str, source: str | None) -> None:
    threading.Thread(
        target=_ingest, args=(version_id, source), daemon=True
    ).start()


def _ingest(version_id: str, source: str | None) -> None:
    from infrastructure.db.session import SessionLocal
    from infrastructure.db.hexagon_query_repository import PostgresHexagonSource

    db = SessionLocal()
    try:
        repo.update(db, version_id, {"status": "updating"})
        n_hex = tile_service.update_from_parquet(db, source)  # ingesta destructiva
        n_var = len(PostgresHexagonSource(db).variable_names())
        repo.update(db, version_id, {
            "status": "ready",
            "applied_at": _now(),
            "hex_count": n_hex,
            "var_count": n_var,
            "notes": None,
        })
        log.info("Master actualizada: %s hex · %s variables (versión %s).",
                 n_hex, n_var, version_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("Falló la actualización de la master.")
        repo.update(db, version_id, {"status": "failed", "notes": str(exc)[:500]})
    finally:
        db.close()


# ── util ──────────────────────────────────────────────────────────────────────

def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _label(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
