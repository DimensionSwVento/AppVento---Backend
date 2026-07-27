"""
Query Engine.
Orquestador puro: recibe un Query, coordina los 4 componentes, devuelve list[QueryResult].
No tiene lógica propia — delega en spatial_resolver, aggregator y variable_engine.
"""
from __future__ import annotations

from domain.query_engine.models import Query, QueryResult
from domain.query_engine import spatial_resolver
from domain.query_engine import aggregator
from domain.query_engine import variable_engine


def execute(query: Query) -> list[QueryResult]:
    """
    Flujo completo:
    1. Spatial resolver  → list[_IndexedHex] dentro de la geometría
    2. Aggregator        → agrupa si la resolución pedida es menor a la del índice
    3. Variable engine   → extrae y normaliza el valor de cada hexágono
    4. Ensambla QueryResult
    """
    from infrastructure.spatial import tile_index as idx

    # 1. Resolución espacial
    hexes = spatial_resolver.resolve(
        query,
        get_at_resolution=idx._get_at_resolution,
        hex_lookup=idx._hex_lookup or {},
    )

    # 2. Agregación (solo si resolución explícita y diferente a la del índice)
    if query.resolution is not None:
        hexes = aggregator.aggregate(hexes, query.resolution)

    # 3. Contexto de variable
    available = idx.get_available_variables()
    var_range = idx.get_variable_range(query.variable) if query.variable else None

    # 4. Construir resultados
    results: list[QueryResult] = []
    for hx in hexes:
        value_raw, value_norm = (None, None)

        if query.variable:
            value_raw, value_norm = variable_engine.resolve_value(
                hx,
                query.variable,
                available,
                var_range,
                query.mode,
            )

        results.append(QueryResult(
            h3_index=hx.h3_index,
            lat=hx.lat,
            lng=hx.lng,
            score=hx.score_general,
            tipo_celda=hx.tipo_celda,
            value=value_raw,
            value_norm=value_norm,
        ))

    return results
