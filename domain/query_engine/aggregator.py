"""
Aggregator.
Recibe lista de _IndexedHex y una resolución target.
Si target_res < resolución del índice (8), agrupa por cell_to_parent y promedia.
Si target_res == 8, pasa directo sin costo.
"""
from __future__ import annotations
from typing import Optional
import h3

from domain.query_engine.source import HexRow


def aggregate(hexes: "list[HexRow]", target_res: int) -> "list[HexRow]":
    """
    Agrupa hexágonos al target_res pedido.
    Si target_res es igual a la resolución de los hexágonos de entrada, devuelve tal cual.
    """
    if not hexes:
        return []

    current_res = h3.get_resolution(hexes[0].h3_index)

    if current_res == target_res:
        return hexes

    groups: dict[str, dict] = {}

    for hx in hexes:
        try:
            parent = h3.cell_to_parent(hx.h3_index, target_res)
        except Exception:
            continue

        if parent not in groups:
            groups[parent] = {
                "total_score": 0.0,
                "count": 0,
                "tipo": hx.tipo_celda,
                "var_totals": {},
                "var_texts": {},
            }

        g = groups[parent]
        g["total_score"] += hx.score_general
        g["count"] += 1

        for k, v in hx.variables.items():
            num: Optional[float] = None
            if isinstance(v, (int, float)):
                num = float(v)
            elif isinstance(v, str):
                try:
                    num = float(v)
                except ValueError:
                    pass

            if num is not None:
                if k not in g["var_totals"]:
                    g["var_totals"][k] = {"sum": 0.0, "count": 0}
                g["var_totals"][k]["sum"] += num
                g["var_totals"][k]["count"] += 1
            elif isinstance(v, str) and k not in g["var_texts"]:
                g["var_texts"][k] = v

    result: list = []
    for hex_id, g in groups.items():
        lat, lng = h3.cell_to_latlng(hex_id)
        variables: dict = {}
        for k, vt in g["var_totals"].items():
            variables[k] = vt["sum"] / vt["count"]
        for k, vt in g["var_texts"].items():
            variables[k] = vt

        result.append(HexRow(
            h3_index=hex_id,
            score_general=g["total_score"] / g["count"],
            tipo_celda=g["tipo"],
            lat=lat,
            lng=lng,
            variables=variables,
        ))

    return result
