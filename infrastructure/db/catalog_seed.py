"""
Config BASE del proyecto (idempotente, solo si la tabla está vacía). Aquí van los
datos que NO se ingieren del cliente sino que son cimiento del software:
  - `escenarios_seed.csv`  → escenarios económicos (con mult/impacto).
  - `parquet_column_seed.csv` → registro de clases (bootstrap de la ingesta).
Si falta el archivo, avisa y omite (no hardcodea fallback).

⚠️ Los DATOS DEL CLIENTE NO se siembran aquí: en modo backend la única fuente de
verdad es la BD, poblada por INGESTA (h3/csv/xlsx/json). Si no se ha ingerido,
NO hay nada — ni variables ni NORMAS (real o vacío, sin proxy que falsee):
  - `catalog_variable` → ingesta de InfoVariables (`metadata_ingest`, por `master_id`).
  - `catalog_norma`    → ingesta de InfoVariables regulatoria (REG… → catalog_norma).
"""
from __future__ import annotations
import csv
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import Escenario, ParquetColumn

log = logging.getLogger("vento")

_DATA = Path(__file__).resolve().parent.parent / "data"
_ESCENARIOS_CSV = _DATA / "escenarios_seed.csv"
_PARQUET_COLUMN_CSV = _DATA / "parquet_column_seed.csv"


def _read_csv(path: Path) -> list[dict]:
    """Lee un CSV de datos → lista de dicts. [] si no existe (avisa)."""
    if not path.exists():
        log.warning("Seed: falta %s → tabla no sembrada (debe venir del archivo).", path.name)
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def seed_catalog(db: Session) -> None:
    """Siembra la CONFIG BASE (escenarios/turbinas/registro de clases) desde sus
    archivos. VARIABLES y NORMAS no se siembran: los puebla la ingesta del cliente."""
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
