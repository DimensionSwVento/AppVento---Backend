"""
Semilla del catálogo de administración (idempotente). NADA está hardcodeado en
Python: todos los valores se INGIEREN de archivos de datos en `infrastructure/
data/` (mismo principio que la master), para que el equipo los pueda actualizar
sin tocar código:
  - `variables_seed.json`  → catálogo de variables (61 modelo + 30 informativa).
  - `normas_seed.csv`      → normas regulatorias.
  - `escenarios_seed.csv`  → escenarios económicos (con mult/impacto).
  - `turbines_seed.csv`    → aerogeneradores S/M/L.
Solo inserta si la tabla está vacía. Si falta el archivo, avisa y omite (no
hardcodea un fallback: el dato real debe venir del archivo).
"""
from __future__ import annotations
import csv
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import CatalogVariable, Escenario, Norma, Turbine, ParquetColumn

log = logging.getLogger("vento")

_DATA = Path(__file__).resolve().parent.parent / "data"
_VARIABLES_SEED = _DATA / "variables_seed.json"
_NORMAS_CSV = _DATA / "normas_seed.csv"
_ESCENARIOS_CSV = _DATA / "escenarios_seed.csv"
_TURBINES_CSV = _DATA / "turbines_seed.csv"
_PARQUET_COLUMN_CSV = _DATA / "parquet_column_seed.csv"


def _read_csv(path: Path) -> list[dict]:
    """Lee un CSV de datos → lista de dicts. [] si no existe (avisa)."""
    if not path.exists():
        log.warning("Seed: falta %s → tabla no sembrada (debe venir del archivo).", path.name)
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _seed_variables(db: Session) -> None:
    """Carga el catálogo rico de variables (si la tabla está vacía)."""
    if db.scalar(select(CatalogVariable.id).limit(1)) is not None:
        return
    if not _VARIABLES_SEED.exists():
        log.warning("Seed: falta variables_seed.json (correr `python -m tools.gen_variables_seed`).")
        return
    entries = json.loads(_VARIABLES_SEED.read_text(encoding="utf-8"))
    db.add_all(CatalogVariable(**e) for e in entries)
    log.info("Seed: %s variables insertadas.", len(entries))


def seed_catalog(db: Session) -> None:
    """Inserta variables/normas/escenarios/turbinas desde los archivos de datos."""
    _seed_variables(db)

    if db.scalar(select(Norma.id).limit(1)) is None:
        rows = _read_csv(_NORMAS_CSV)
        db.add_all(
            Norma(id=r["id"], nombre=r["nombre"],
                  descripcion=r["descripcion"], estado=r["estado"])
            for r in rows
        )
        if rows:
            log.info("Seed: %s normas insertadas (normas_seed.csv).", len(rows))

    if db.scalar(select(Escenario.id).limit(1)) is None:
        rows = _read_csv(_ESCENARIOS_CSV)
        db.add_all(
            Escenario(id=r["id"], label=r["label"], description=r["description"],
                      horizonte=int(r["horizonte"]), mult=float(r["mult"]),
                      impacto=r["impacto"])
            for r in rows
        )
        if rows:
            log.info("Seed: %s escenarios insertados (escenarios_seed.csv).", len(rows))

    if db.scalar(select(Turbine.id).limit(1)) is None:
        rows = _read_csv(_TURBINES_CSV)
        db.add_all(
            Turbine(id=r["id"], label=r["label"], detail=r["detail"],
                    potencia_min_mw=float(r["potencia_min_mw"]),
                    potencia_max_mw=float(r["potencia_max_mw"]),
                    area_por_aero_km2=float(r["area_por_aero_km2"]),
                    espaciamiento_m=int(r["espaciamiento_m"]))
            for r in rows
        )
        if rows:
            log.info("Seed: %s turbinas insertadas (turbines_seed.csv).", len(rows))

    if db.scalar(select(ParquetColumn.name).limit(1)) is None:
        rows = _read_csv(_PARQUET_COLUMN_CSV)
        db.add_all(
            ParquetColumn(name=r["name"], base_code=r["base_code"], kind=r["kind"],
                          target_ref=r["target_ref"], usos=r["usos"],
                          tipo_dato=r["tipo_dato"], estado=r["estado"], nota=r["nota"])
            for r in rows
        )
        if rows:
            log.info("Seed: %s columnas de registro (parquet_column_seed.csv).", len(rows))

    db.commit()
