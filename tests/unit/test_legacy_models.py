"""
Cobertura del motor legado `domain/query_engine/models.py` (dataclasses sin uso,
ver docs/architecture/backend-api.md §2). Se instancian para no dejar el módulo muerto.
"""
from __future__ import annotations

from domain.query_engine.models import Query, QueryResult, TileXYZ


def test_legacy_query_models_instantiate():
    tile = TileXYZ(z=1, x=2, y=3)
    assert (tile.z, tile.x, tile.y) == (1, 2, 3)

    query = Query(type="area", geometry=[(1.0, 2.0)], variable="TEC_001",
                  resolution=8, mode="normalized")
    assert query.type == "area"
    assert query.mode == "normalized"

    result = QueryResult(h3_index="h", lat=1.0, lng=2.0, score=0.5,
                         tipo_celda="disponible", value=3.0, value_norm=0.5)
    assert result.h3_index == "h"
    assert result.value_norm == 0.5
