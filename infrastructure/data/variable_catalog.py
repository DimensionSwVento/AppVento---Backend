"""
Catálogo de variables: normaliza los CÓDIGOS del parquet a NOMBRES legibles y a
su dimensión.

El parquet viene con códigos y **sub-variantes** (`svNNN`), p. ej.:
    TEC_001_sv001 .. TEC_001_sv005   → variable base `TEC_001` (5 sub-variantes)
    TEC_004_sv001 .. TEC_004_sv005   → variable base `TEC_004`
    TEC_005, REG_001, SOE_001, …     → variables sin sub-variante

`base_code` colapsa la sub-variante; `LABELS` mapea el código base a su nombre.

⚠️ `LABELS` es un STUB — llenar con el **catálogo real** del equipo cuando esté
cerrado. Mientras esté vacío, se usa un fallback legible del propio código.
"""
from __future__ import annotations

# Dimensión (categoría) por prefijo.
DIMENSIONS: dict[str, str] = {
    "TEC": "Técnica",
    "AMB": "Ambiental",
    "SOE": "Socioeconómica",
    "MAC": "Macroeconómica",
    "REG": "Regulatoria",
}

# Código base → nombre legible.  ⚠️ PLACEHOLDER: completar con el catálogo real.
# Ejemplos (descomentar/ajustar con los nombres definitivos):
LABELS: dict[str, str] = {
    # "TEC_001": "Velocidad de viento",
    # "TEC_004": "Densidad de energía",
    # "TEC_005": "…",
    # "SOE_001": "…",
    # "REG_001": "…",
}


def base_code(code: str) -> str:
    """
    Reduce un código a su base `DIM_NNN`, quitando TODA variante/sufijo del parquet
    (`_svNNN`, `_onshore`, `_dist`, `_t`, `_alert`, `_s`, `_sr`, `_se`, `_tse`,
    `_ser`, …). Así casa la columna de la master con el `master_id` del catálogo.

        TEC_004_sv003 → TEC_004    TEC_023_onshore → TEC_023
        TEC_032_dist  → TEC_032    SOC_010_tse     → SOC_010
        TEC_001_alert → TEC_001    AMB_005         → AMB_005

    Códigos que NO son `DIM_NNN` (h3_index, global, recurso_eolico, excluido,
    alerta) se devuelven tal cual.
    """
    parts = code.split("_")
    if len(parts) >= 2 and parts[0].isalpha() and parts[1].isdigit():
        return f"{parts[0]}_{parts[1]}"
    return code


def dimension_of(code: str) -> str | None:
    """Prefijo de dimensión del código (`TEC`, `AMB`, …) o None si no aplica."""
    prefix = code.split("_", 1)[0]
    return prefix if prefix in DIMENSIONS else None


def get_variable_label(code: str) -> str:
    """Nombre legible de una variable (por su código base). Fallback: el código."""
    base = base_code(code)
    return LABELS.get(base, base.replace("_", " "))
