---
version: 1.0
date: 2026-08-23
author: mavalan Software
scope: estrategia de testing del backend Vento (FastAPI)
---

# Estrategia de Testing — Backend Vento

Documento espejo de `test-strategy.md` del repo del frontend (`AppVento`), que responde
al mismo requerimiento de trazabilidad **RNF → tests** (sección 8.2.2 del documento de
diseño), aplicado al backend FastAPI. La guía práctica del día a día (cómo correr,
convención de ubicación, patrones) está en [`backend-testing.md`](backend-testing.md).

---

## 1. Resumen ejecutivo

- **125 tests** (96 unit + 29 de integración) cubriendo geometría H3, scoring,
  motor de variables, catálogo, lectura del parquet, persistencia (PostgreSQL real),
  autenticación y la API.
- **Framework**: pytest + pytest-cov (cobertura) + httpx (`TestClient`) + ruff (lint).
- **Ubicación doble**:
  - `tests/unit/**` — lógica pura y servicios con `HexagonSource` fake (sin BD).
  - `tests/integration/**` — API y persistencia; los tests de BD llevan `@pytest.mark.postgres`
    y se ejecutan contra un **PostgreSQL real levantado con Docker**.
- **Cobertura**: **94%** (umbral mínimo exigido por el proyecto: **80%**, `fail_under` en `pyproject.toml`).
- **Ejecución**:
  - Local: `docker compose up -d db` + `pytest` / `pytest --cov`.
  - CI: `.github/workflows/backend-ci.yml` (lint → unit+integración+cobertura → build → secret scan);
    el pipeline falla si baja de 80% o si algún test falla.

---

## 2. Stack de testing

| Herramienta | Versión | Propósito |
|---|---|---|
| [pytest](https://pytest.org) | ~8.x | Test runner + assertions + fixtures + markers |
| [pytest-cov](https://pytest-cov.readthedocs.io) | ~5.x | Cobertura (reporte `text`, umbral `fail_under`) |
| [httpx](https://www.python-httpx.org) / `fastapi.testclient.TestClient` | — | Tests de API sin servidor real |
| [ruff](https://ruff.rs) | ~0.x | Linter (reglas `E4/E7/E9/F`, `E701` ignorado) |
| [pyarrow](https://arrow.apache.org) | — | Generación de `.h3.parquet` de prueba en `tmp_path` |
| [h3-py](https://github.com/uber/h3-py) | 4.x | Índices H3 reales (sin mocks) |
| SQLAlchemy + **PostgreSQL 16 (Docker)** | — | Integración: `tests/integration/conftest.py` (`db_engine`, `db`, `session_factory`) |

**Configuración**: `pyproject.toml` — secciones `[tool.pytest.ini_options]` (marker `postgres`,
`filterwarnings`), `[tool.coverage.*]` (`source`, `branch`, `fail_under = 80`), `[tool.ruff]`.

**Principio clave**: el backend es **stateless** y depende del puerto `HexagonSource`
(`domain/query_engine/source.py`). Los tests de la capa de aplicación usan una implementación
fake en memoria (`tests/conftest.py::FakeHexagonSource`); los tests de integración reemplazan
esa fake por `PostgresHexagonSource` contra una BD real.

---

## 3. Inventario completo de tests

Organizado por los **RNF priorizados** (sección 8.2.2). El detalle de qué verifica cada
grupo está en [`backend-testing.md`](backend-testing.md#trazabilidad-rnf--tests).

### RNF-1 · Disponibilidad

| Archivo | Qué prueba |
|---|---|
| `tests/integration/test_api.py` | `GET /api/health` (index_ready), `GET /api/tiles/warmup` (metadatos) |
| `tests/integration/test_hexagon_query_repository.py` | `is_ready()`, `fetch_*`, `variable_*`, `count`/`counts` |
| `tests/integration/test_repository.py` | provisión de usuario (`ensure_user`) y CRUD de informes |
| `tests/integration/test_historial_service.py` | servicio de histórico (list/save/delete) |

### RNF-2 · Integración / Interoperabilidad

| Archivo | Qué prueba |
|---|---|
| `tests/unit/test_geo.py` | contrato tile `z/x/y` ↔ bbox (web mercator) |
| `tests/integration/test_api.py` | shape compacto `TileHex {h,s,t,la,ln,v}` |
| `tests/unit/test_parquet_reader.py` | consumo del estándar `.h3.parquet` y derivación de `lat/lng` |
| `tests/unit/test_keycloak_guard.py` | identidad OIDC (JWT de Keycloak) y vía interna del BFF |

### RNF-3 · Procesamiento

| Archivo | Qué prueba |
|---|---|
| `tests/unit/test_geo.py` | `tile_to_bbox`, `polygon_to_cells`, `expand_to_index_res` |
| `tests/unit/test_aggregator.py` | agregación de hexágonos a la resolución del zoom |
| `tests/unit/test_scoring.py` | `zoom_to_h3_res`, `calc_temp_viability`, `effective_score` |
| `tests/unit/test_variable_engine.py` | resolución/lectura/normalización de variables |
| `tests/unit/test_tile_service.py` | servicio stateless (tile, detalle, área, heatmap, counts) |

### RNF-4 · Calidad de datos

| Archivo | Qué prueba |
|---|---|
| `tests/unit/test_scoring.py` | `is_in_colombia` (completitud territorial) |
| `tests/unit/test_variable_catalog.py` | `base_code`, `dimension_of`, `get_variable_label` |
| `tests/unit/test_parquet_reader.py` | nulos/NaN → defaults, score efectivo |
| `tests/integration/test_hexagon_repository.py` | sanitizado numpy/NaN, `_accumulate_meta`, ingesta ORM + COPY |
| `tests/integration/test_ingest.py` | `update_from_parquet` (orm y copy) con filtro Colombia |

### RNF-5 · Usabilidad geoespacial (aporte del backend)

| Archivo | Qué prueba |
|---|---|
| `tests/integration/test_api.py`, `tests/unit/test_tile_service.py` | shapes de respuesta estables y completos que consume la UI |

---

## 4. Qué NO está cubierto aún

| Área | Estado | Prioridad |
|---|---|---|
| Métodos abstractos del `Protocol HexagonSource` (cuerpos `...`) | No cubrible (interfaz) | — |
| `session.get_db` / `init_db` (wiring de la app) | Parcial (se cubre vía integración) | Baja |
| Ramas de error de `hexagon_repository.copy_ingest` / `update` | Parcial | Baja |
| E2E contra **Keycloak real** (login OIDC de extremo a extremo) | ❌ No implementado | Media — el frontend ya lo cubre en `e2e/login-flow.spec.ts` |
| Validación del **modelo multicriterio real** (AHP/pesos) | ❌ Externo | El score placeholder se documenta como temporal |

---

## 5. Cómo ejecutar los tests

### Local
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d db     # PostgreSQL 16 en localhost:5432

pytest                       # toda la suite (unit + integración)
pytest -m "not postgres"     # solo unit (sin BD)
pytest -m postgres           # solo integración (BD)
pytest --cov                 # suite + cobertura (umbral 80%)
ruff check                   # lint
```

### CI
`.github/workflows/backend-ci.yml` corre en PRs a `develop`/`main` y push a `main`:
`lint → unit+integración+cobertura (PostgreSQL como service container) → build → secret scan`.
Sin secrets ni actions de marketplace (checkout con `git` + `github.token`).

---

## 6. Criterio de "done" para un test

1. **Pasa** en local y CI.
2. **Es legible en 30s**: un lector entiende qué espera y por qué sin bajar a implementación.
3. **Falla por la razón correcta**: si borras la lógica que prueba, debe fallar; si cambias un
   detalle interno irrelevante, NO debe fallar.
4. **No depende del orden ni del tiempo**: usa fixtures y `pytest.approx`; nada de `sleep` arbitrario.
5. **Aislamiento**: los tests de BD limpian sus tablas (fixture `autouse` en `tests/integration/conftest.py`).

---

## 7. Flujo al añadir un test nuevo

```
1. Identificar qué capa prueba:
   - Lógica pura / servicio con HexagonSource fake  → tests/unit/
   - API (TestClient) o persistencia (Postgres)      → tests/integration/  (+ @pytest.mark.postgres si toca BD)
2. Crear test_*.py con describe/it centrado en comportamiento, no implementación.
3. Correr local: docker compose up -d db && pytest
4. Si sube la cobertura de forma estable: sube `fail_under` en el mismo PR (política ratchet).
5. Al verde, commit → push → CI valida en pipeline.
```

---

## 8. Convenciones y anti-patrones

### Hacer
- Tests pequeños (1 test = 1 comportamiento).
- Fixtures compartidas en `tests/conftest.py` (`FakeHexagonSource`, `hexrow`) y
  `tests/integration/conftest.py` (`db_engine`, `db`, `session_factory`).
- Datos de prueba en constantes al top del archivo.
- `@pytest.mark.postgres` para TODO test que requiera la BD real.

### Evitar
- Mockear lo que se quiere probar (si pruebas `tile_service`, no mockees `tile_service`,
  mockea la fuente de datos).
- Depender de Postgres en `tests/unit/` — usa `FakeHexagonSource`.
- Hardcodear URLs/productivos o secretos reales en tests (usa `X-Internal-Secret` de prueba
  y el realm dev de Keycloak).
- Bajar `fail_under` para "arreglar" CI.

---

## 9. Referencias cruzadas

- Guía práctica y trazabilidad RNF → tests: [backend-testing.md](backend-testing.md).
- Estrategia de testing del frontend (espejo): `test-strategy.md` en el repo `AppVento` (carpeta `docs/testing/`).
- Referencia técnica del backend: `backend-api.md` en el repo `AppVento` (carpeta `docs/architecture/`).
- CI: `.github/workflows/backend-ci.yml`.

---

## 10. Roadmap de testing

| Etapa | Objetivo |
|---|---|
| **Actual** | Cobertura 94% con unit + integración (Postgres real); umbral 80% |
| **v1.1** | E2E contra Keycloak real (login OIDC → endpoint protegido) |
| **v1.2** | Test de performance de `copy_ingest` sobre el dataset completo (~1.6M hexágonos) |
| **v1.3** | Sustituir el score placeholder por el modelo AHP real y ajustar los tests de scoring |
