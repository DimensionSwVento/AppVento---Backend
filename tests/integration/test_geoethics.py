"""
Geoethics — provenance and licensing of the spatial variables the platform serves.

The catalog of variables (`catalog_variable`) is not seeded from the repository:
it is populated by ingesting the client's InfoVariables document
(`metadata_ingest.apply`) and served to the app by `catalog_service.list_variables`
and `metadata_ingest.hierarchy`. These tests go through that real path with an
InfoVariables-shaped CSV.

Catalog tests implemented (`geoethics`):
- "data origin validation": source name and URL of every variable (and of the
  regulatory norms) survive ingest and are served unchanged; URLs are well formed.
- "license validation": no variable is served without a declared source, source
  URL and license.
"""
from __future__ import annotations

import csv
from urllib.parse import urlparse

import pytest

from application.services import catalog_service, metadata_ingest

pytestmark = [pytest.mark.qa_geoethics, pytest.mark.postgres]

_HEADERS = ["Dimensión", "Criterio", "Subcriterio", "ID", "Variable", "Descripción",
            "Fuente", "URL", "Tipo de licencia"]

COMPLETE_ROWS = [
    ["Técnica", "Recurso eólico", "Densidad WPD", "TEC-001-A", "Densidad de potencia",
     "Densidad de potencia del viento a 100 m", "Global Wind Atlas (DTU)",
     "https://globalwindatlas.info", "CC BY 4.0"],
    ["Ambiental", "Contexto biótico", "Áreas protegidas", "AMB-005-A", "Áreas protegidas",
     "Registro Único Nacional de Áreas Protegidas", "Parques Nacionales Naturales de Colombia",
     "https://runap.parquesnacionales.gov.co", "Datos abiertos Colombia"],
    ["Social", "Entorno social", "Población", "SOC-010-SE", "Densidad poblacional",
     "Habitantes por km2 (CNPV 2018)", "DANE", "https://www.dane.gov.co", "Datos abiertos Colombia"],
]
NORMA_ROW = ["Regulatoria", "Ambiental", "Licenciamiento", "REG-001-L", "Ley 99 de 1993",
             "Licencia ambiental para proyectos de generación", "Congreso de Colombia",
             "http://www.secretariasenado.gov.co/senado/basedoc/ley_0099_1993.html", ""]

# A row as it arrives when the provider leaves the provenance cells empty.
INCOMPLETE_ROW = ["Técnica", "Recurso eólico", "Turbulencia", "TEC-004-A", "Intensidad de turbulencia",
                  "Intensidad de turbulencia a 100 m", "", "", ""]

# Values the ORM uses when nothing is declared: they are placeholders, not provenance.
_PLACEHOLDERS = {"", "por definir", "na", "n/a", "-"}


def _ingest(db, tmp_path, rows) -> dict:
    path = tmp_path / "InfoVariables.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HEADERS)
        w.writerows(rows)
    result = metadata_ingest.apply(db, str(path))
    assert "error" not in result, result
    return result


def _served_variables(db) -> list[dict]:
    """Variables exactly as the app receives them, from both read paths."""
    flat = catalog_service.list_variables(db, None)
    nested = [
        v
        for dim in metadata_ingest.hierarchy(db)
        for cri in dim.get("criterios", [])
        for sub in cri.get("subcriterios", [])
        for v in sub.get("variables", [])
    ]
    return flat + nested


def _is_url(value: str) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# ── Data origin validation ───────────────────────────────────────────────────

def test_ingested_variables_are_served_with_their_declared_origin(db, tmp_path):
    _ingest(db, tmp_path, COMPLETE_ROWS)
    declared = {r[3]: {"fuente": r[6], "url": r[7], "licencia": r[8]} for r in COMPLETE_ROWS}

    served = _served_variables(db)
    assert {v["masterId"] for v in served} == set(declared)
    for v in served:
        expected = declared[v["masterId"]]
        assert v["fuente"] == expected["fuente"]
        assert v["url"] == expected["url"]
        assert v["licencia"] == expected["licencia"]
        assert _is_url(v["url"]), v["url"]


def test_regulatory_norms_are_served_with_their_declared_origin(db, tmp_path):
    result = _ingest(db, tmp_path, COMPLETE_ROWS + [NORMA_ROW])
    assert result["normas"] == 1

    [norma] = [n for n in catalog_service.list_normas(db) if n["id"] == "REG-001-L"]
    assert norma["fuente"] == NORMA_ROW[6]
    assert norma["url"] == NORMA_ROW[7]
    assert _is_url(norma["url"])


# ── License validation ───────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="D9/#34: no hay validación de procedencia/licencia; el default "
           "'Por definir' hace que la variable se sirva igual",
)
def test_no_variable_is_served_without_origin_and_license(db, tmp_path):
    """Invariant: whatever the ingested document contains, the catalog the app
    receives never exposes a variable whose source, source URL or license is
    missing — such a row must be rejected or held back at ingest."""
    _ingest(db, tmp_path, COMPLETE_ROWS + [INCOMPLETE_ROW])

    offenders = [
        f"{v['masterId']}: fuente={v['fuente']!r} url={v['url']!r} licencia={v['licencia']!r}"
        for v in _served_variables(db)
        if (v["fuente"] or "").strip().lower() in _PLACEHOLDERS
        or not _is_url(v["url"])
        or (v["licencia"] or "").strip().lower() in _PLACEHOLDERS
    ]
    assert not offenders, "variables served without provenance/license:\n" + "\n".join(offenders)
