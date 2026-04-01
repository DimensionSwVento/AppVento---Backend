"""
Parquet reader — migrado desde parseH3Parquet.ts
Lee master_variables.h3.parquet con pandas+pyarrow (vectorizado).
"""
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from domain.models.hexagon import HexagonData

_IGNORE_COLUMNS = {
    "h3_index", "hex_id", "score_general", "score", "tipo_celda", "cell_type",
}
_IGNORE_PREFIXES = ("divipola_csv", "lat_lon_csv", "municipios_csv")

_VARIABLE_LABELS: dict[str, str] = {
    "001_velocidad_de_viento": "Velocidad de Viento (m/s)",
    "004_densidad_energ": "Densidad Energética Eólica (W/m²)",
    "009_profundidad_roca": "Profundidad de Roca",
    "soc_022_sr": "Zonas Reserva Campesina (ha)",
    "soc_025_er": "Cigarrillos Ilegales (%)",
}


def get_variable_label(col_name: str) -> str:
    for prefix, label in _VARIABLE_LABELS.items():
        if col_name.startswith(prefix):
            return label
    parts = col_name.split("/")
    return parts[0].replace("_", " ")


def _should_ignore(col: str) -> bool:
    if col in _IGNORE_COLUMNS:
        return True
    return any(col.startswith(p) for p in _IGNORE_PREFIXES)


def read_parquet(file_path: str) -> tuple[list[HexagonData], list[str], str]:
    """
    Returns (hexagons, variable_names, parsed_at).
    Usa to_dict("records") en vez de itertuples — mucho más rápido para 150K+ filas.
    """
    df = pd.read_parquet(file_path, engine="pyarrow")

    variable_names = [col for col in df.columns if not _should_ignore(col)]

    # columnas de identidad
    h3_col = "h3_index" if "h3_index" in df.columns else "hex_id"
    score_col = "score_general" if "score_general" in df.columns else "score"
    tipo_col = "tipo_celda" if "tipo_celda" in df.columns else "cell_type"

    # reemplazar NaN por None en variables
    var_df = df[variable_names].where(df[variable_names].notna(), other=None)
    records = var_df.to_dict("records")

    h3_values = df[h3_col].astype(str).values if h3_col in df.columns else [""] * len(df)
    score_values = pd.to_numeric(df[score_col], errors="coerce").fillna(0).values if score_col in df.columns else np.zeros(len(df))
    tipo_values = df[tipo_col].astype(str).values if tipo_col in df.columns else ["activa"] * len(df)

    hexagons: list[HexagonData] = []
    for i in range(len(df)):
        variables: dict[str, str | float | None] = {}
        for k, v in records[i].items():
            if v is None:
                variables[k] = None
            elif isinstance(v, (int, float, np.integer, np.floating)):
                variables[k] = float(v)
            else:
                variables[k] = str(v)

        hexagons.append(HexagonData(
            h3_index=str(h3_values[i]),
            score_general=float(score_values[i]),
            tipo_celda=str(tipo_values[i]),
            variables=variables,
        ))

    parsed_at = datetime.now(timezone.utc).isoformat()
    return hexagons, variable_names, parsed_at
