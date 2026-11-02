# Quality attributes pipeline (backend)

Backend counterpart of the frontend protocol (AppVento,
`docs/testing/quality-attributes-pipeline.md`): every test serves one quality
attribute of a shared catalog, gates group the tests per attribute, and one run
produces one evidence bundle plus a deterministic deployment decision.

## Shared catalog

`quality/quality-attributes.json` (catalog v1.0.0, 13 attributes) is an **exact
copy** of the frontend file, together with `quality-attributes.schema.json` and
`evidence-bundle.schema.json`. **The frontend file is the source of truth**:
change the catalog there and copy it here unchanged. Never edit it in this repo.

## Pieces

| File | Role |
| --- | --- |
| `pyproject.toml` | registers one marker `qa_<attribute_id>` per catalog attribute (`-` → `_`) |
| `tests/conftest.py` | rejects any test with zero or more than one `qa_*` marker, so markers partition the suite |
| `quality/gates.json` | gate inventory: id, attribute, source, the catalog tests it implements, blocking |
| `quality/gates.schema.json` | frontend gate schema + the `pytest` source kind (`{"kind": "pytest", "markers": "qa_resilience"}`) and the `postgres` prerequisite |
| `quality/scripts/collect_evidence.py` | validate → coverage inventory → run gates → decide → `quality/evidence/run-<id>.json` |
| `.github/workflows/backend-quality-pipeline.yml` | runs it on push to `research/quality-pipeline-v2` and on demand |

## Gates

| Gate | Attribute | Catalog tests implemented |
| --- | --- | --- |
| `ruff-lint` | processing | — |
| `spatial-processing-tests` | processing | spatial unit tests, CRS consistency, reproducibility |
| `data-source-availability-tests` | availability | connectivity to data sources, GIS service recovery |
| `bundle-integration-tests` | data-integration | multi-source integration, semantic consistency, metadata validation |
| `reference-result-tests` | result-quality | validation against reference data, topological consistency |
| `auth-rbac-tests` | security | layer access control protection, GIS API security |
| `spatial-data-quality-tests` | data-quality | spatial completeness, metadata validation |
| `api-contract-tests` | interoperability | standards and schema validation, import/export format testing |
| `traceability-tests` | traceability-auditability | data versioning, spatial data lineage, historical reproducibility |
| `fault-injection-tests` | resilience | controlled failure (fault injection), recovery testing |
| `provenance-license-tests` | geoethics | data origin validation, license validation |

Coverage rule (same as the frontend): an attribute is **covered** when its gates
implement at least 50% of its catalog tests, **weak** with at least one gate
below that, **uncovered** with no gate. Current inventory: 10 covered, 0 weak,
3 uncovered (performance, scalability, usability-geospatial).

- **Performance and scalability** are deliberately not tested here: their
  evidence will come from the k6 load-test suite (`perf/`), owned separately.
  No `qa_performance` / `qa_scalability` tests exist in pytest.
- **Usability (geospatial)** has no backend surface; it is covered by the frontend.

The golden file of `reference-result-tests`
(`tests/unit/golden/area_report_reference.json`) holds values derived **by hand**
from the WLC/AHP formula in `infrastructure/data/model_weights.py`. If the real
AHP weights land, re-derive it by hand; do not regenerate it from the code. The
OpenAPI snapshot (`tests/unit/snapshots/openapi_public.json`) is regenerated only
for a deliberate contract change: `UPDATE_OPENAPI_SNAPSHOT=1 pytest tests/unit/test_openapi_contract.py`.

## Running it

```bash
U=postgresql+psycopg://vento:vento@localhost:5432/vento   # same DB for app and tests
DATABASE_URL=$U TEST_DATABASE_URL=$U python quality/scripts/collect_evidence.py --run-id local-002
python quality/scripts/collect_evidence.py --validate-only   # inventory + coverage only
DATABASE_URL=$U TEST_DATABASE_URL=$U pytest -m qa_resilience  # one attribute
```

Per gate the bundle records `testsRun`, `testsPassed`, `assertions` (static count
of `assert` / `pytest.raises` in the executed test functions — pytest reports no
assertion counts), `durationMs`, `logExcerpt` and the raw junit/log paths. Line
and branch coverage is the union over all pytest gates (pytest-cov). `partition`
checks that the gates executed every collected test. `analysis` is `null`: the
LLM analysis stage of the frontend is not wired in the backend yet. `audit` is
all zeros because no dependency-audit gate exists (`auditNote`).

Decision (deterministic): **block** if any blocking gate failed or produced no
evidence (skipped, e.g. PostgreSQL unreachable); a failing non-blocking gate is a
warning; otherwise **allow**.

## Findings the gates currently block on

These tests fail on purpose; each documents a real defect, not a test bug.

| Gate | Test | Defect |
| --- | --- | --- |
| availability | `test_health_stays_responsive_while_a_master_ingest_is_running` | `copy_ingest` holds TRUNCATE's ACCESS EXCLUSIVE lock on `hexagon` for the whole COPY, so `/api/health` and every map read block until the ingest ends |
| resilience | `test_failed_master_update_is_recorded_and_does_not_block_the_next_one` | after a failed COPY, `master_service._ingest` updates the status inside the aborted transaction; the update raises and the version stays `updating` forever, so every later master update is refused |
| resilience | `test_failed_bundle_ingest_releases_the_ingest_lock` | after a database error, `bundle_service.apply` runs `pg_advisory_unlock` in the aborted transaction; it raises (masking the original error) and the session-level ingest lock leaks on the pooled connection |
| traceability | `test_master_version_labels_identify_a_single_version` | version labels have minute resolution and reports store the label, so two ingests in the same minute are indistinguishable |
| traceability | `test_catalog_document_lineage_records_the_ingested_row_count` | `catalog_ingest.ingest` counts rows before the merged rows are flushed (`autoflush=False`): `data_source.rows` is stale (0 on first ingest) |
| geoethics | `test_no_variable_is_served_without_origin_and_license` | no provenance/license validation: a variable with empty source, URL and license is ingested and served (`fuente='Por definir'`) |

Note that `backend-ci.yml` runs the whole suite on pull requests to
`develop`/`main`, so these tests will also fail there until the defects are fixed.
