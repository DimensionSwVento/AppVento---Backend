# Guía de testing — Vento Backend

## Stack

- **pytest** (runner) + **pytest-cov** (cobertura) + **httpx** (cliente para tests de API vía `TestClient`).
- **ruff** como linter (reglas por defecto `E4/E7/E9/F`; ver `pyproject.toml`).
- Configuración: `pyproject.toml` (secciones `[tool.pytest.ini_options]`, `[tool.coverage.*]`, `[tool.ruff]`).
- Los tests NO necesitan Postgres ni Keycloak: el backend es stateless y depende del
  puerto `HexagonSource` (`domain/query_engine/source.py`), que en los tests se reemplaza
  por una implementación fake en memoria (`tests/conftest.py::FakeHexagonSource`).

## Cómo correr los tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Los tests de integración necesitan PostgreSQL real:
docker compose up -d db        # expone Postgres en el puerto 5432

pytest               # toda la suite (unit + integración)
pytest -m "not postgres"   # solo unit (sin BD)
pytest --cov         # suite + reporte de cobertura (umbral en pyproject.toml)
ruff check           # linter
```

## Cobertura

- Fuente de cobertura: `domain`, `application`, `infrastructure`, `api`, `shared`.
- Umbral `fail_under = 80` en `pyproject.toml` (requisito mínimo del proyecto).
- **Política ratchet**: cuando la cobertura sube de forma estable, sube el umbral en el mismo PR.
  Nunca lo bajes para "arreglar" CI — agrega tests o justifica un `exclude`.
- Los módulos atados a PostgreSQL (`hexagon_repository`, `repository`,
  `hexagon_query_repository`, `session`) se cubren con tests de integración contra un
  Postgres real (vía Docker), no con mocks.

## Convención de ubicación

| Tipo de test | Ubicación | Ejemplo |
|---|---|---|
| Lógica pura (domain, application, variable_catalog) | `tests/unit/` | `tests/unit/test_geo.py` |
| Infraestructura con datos (parquet_reader) | `tests/unit/` | `tests/unit/test_parquet_reader.py` |
| API FastAPI sin BD (fake source) | `tests/integration/` | `tests/integration/test_api.py` |
| **PostgreSQL real** (repositorios, ingesta, API end-to-end) | `tests/integration/` + `@pytest.mark.postgres` | `tests/integration/test_repository.py` |

Regla práctica: si el test importa una función pura o un servicio con un `HexagonSource`
fake, va en `tests/unit/`. Si ejercita `TestClient` contra `main.app`, o toca Postgres real,
va en `tests/integration/`. Los tests de BD llevan `@pytest.mark.postgres` y usan las
fixtures de `tests/integration/conftest.py` (`db_engine`, `db`, `session_factory`), que
conectan a `TEST_DATABASE_URL` (por defecto `localhost:5432`, el servicio `db` del compose)
y se saltan con un mensaje claro si Postgres no está disponible.

## Trazabilidad RNF → tests

Los RNF priorizados del proyecto (sección 8.2.2 del documento de diseño) y cómo los cubre
la suite del **backend**. La columna "alcance" es honesta: parte de cada RNF vive en el
frontend (Vento/Next.js) y se valida allá; aquí se documenta lo verificable desde este repo.

### RNF-1 · Disponibilidad
*Capacidad del sistema para acceder a datos espaciales cuando se requieren.*

| Qué se verifica | Tests |
|---|---|
| El endpoint de salud responde y reporta `index_ready` según si la fuente tiene datos | `tests/integration/test_api.py::test_health_returns_ok_when_source_ready` |
| El warmup reporta metadatos (count + variables + rangos) sin cargar datos a RAM | `test_warmup_is_open_and_reports_metadata`, `tests/unit/test_tile_service.py::test_warmup_*` |
| Degradación elegante: `is_ready()` devuelve `False` (no lanza) si la BD no responde | `infrastructure/db/hexagon_query_repository.py::is_ready` (try/except) |

**Alcance**: el uptime real del servicio es responsabilidad de ops (systemd/docker, ver
`DEPLOY.md`). Aquí se garantiza que el backend reporta disponibilidad de forma honesta y
sirve los datos cuando existen.

### RNF-2 · Integración / Interoperabilidad
*Combinar datos espaciales heterogéneos de forma coherente; intercambiar y consumir datos y servicios GIS de forma estandarizada.*

| Qué se verifica | Tests |
|---|---|
| Contrato estándar de tiles `z/x/y` (web mercator) y del shape compacto `TileHex {h,s,t,la,ln,v}` | `tests/unit/test_geo.py::test_tile_to_bbox_*`, `tests/integration/test_api.py::test_tile_endpoint_returns_compact_hexes` |
| Indexación H3 estándar con la librería real (`h3-py`, sin mocks): parent/child, agregación por zoom, polígono→celdas | `tests/unit/test_geo.py`, `tests/unit/test_aggregator.py` |
| Consumo del estándar `.h3.parquet` (columnar, `h3_index` + variables) y derivación de `lat/lng` desde el índice | `tests/unit/test_parquet_reader.py` |
| Interoperabilidad de identidad vía OIDC: verificación del JWT de Keycloak (RS256 vía JWKS) y de la vía interna del BFF | `tests/unit/test_keycloak_guard.py` |

**Alcance**: la integración con servicios OGC/geocodificadores externos (Nominatim, IGAC)
vive en el frontend; el backend consume el `.h3.parquet` y expone un contrato tile-based.

### RNF-3 · Procesamiento
*Ejecutar correctamente operaciones y flujos GIS.*

| Qué se verifica | Tests |
|---|---|
| Geometría pura H3: tile↔bbox, polígono→celdas, normalización a la resolución del índice | `tests/unit/test_geo.py` |
| Agregación de hexágonos a la resolución del zoom (promedio de score y variables) | `tests/unit/test_aggregator.py` |
| Mapeo zoom→resolución H3 | `tests/unit/test_scoring.py::test_zoom_to_h3_res` |
| Motor de variables: resolución de categorías, lectura, normalización | `tests/unit/test_variable_engine.py` |
| Servicio de aplicación stateless (tiles, detalle, informe por área con scores por dimensión) | `tests/unit/test_tile_service.py`, `tests/integration/test_api.py::test_area_endpoint_*` |

### RNF-4 · Calidad de datos
*Datos espaciales completos, consistentes y adecuados; confiabilidad del resultado GIS para la toma de decisiones.*

| Qué se verifica | Tests |
|---|---|
| Completitud territorial: puntos de control (Bogotá, Medellín, archipiélago de San Andrés) dentro; fuera (Quito, Caracas) rechazados | `tests/unit/test_scoring.py::test_is_in_colombia_*` |
| Nulos/ausentes no se presentan como datos válidos: `tipo_celda` nulo → `"disponible"`, `score` nulo → placeholder, `NaN` → `None` | `tests/unit/test_parquet_reader.py` |
| Consistencia del catálogo de variables: códigos base, sub-variantes colapsadas, dimensión por prefijo | `tests/unit/test_variable_catalog.py` |
| El filtro geográfico (Colombia) se aplica en la ingesta y descarta celdas fuera del territorio | `tests/unit/test_parquet_reader.py::test_iter_parquet_rows_filters_out_of_colombia` |

**Alcance**: la validez del modelo multicriterio (AHP y pesos) la define el equipo de
investigación; el score placeholder (`calc_temp_viability`) se documenta como temporal. Aquí
se garantiza que la ingesta no degrada el dato ni presenta datos incompletos como completos.

### RNF-5 · Usabilidad geoespacial
*Presentar información espacial comprensible, accesible y cognitivamente adecuada.*

| Qué se verifica | Tests |
|---|---|
| El backend entrega shapes de respuesta estables y completos que la UI puede presentar sin ambigüedad (claves cortas del tile, `scores` por dimensión, `variables` del detalle) | `tests/integration/test_api.py`, `tests/unit/test_tile_service.py` |

**Alcance**: la usabilidad geoespacial (accesibilidad, leyenda, tooltips, carga cognitiva)
se valida en el frontend (ver `AppVento/docs/testing/frontend-testing.md`). El backend
aporta el contrato de datos limpio que hace posible esa presentación.

## CI (GitHub Actions)

`.github/workflows/backend-ci.yml` corre en PRs y push a `main`/`develop` que toquen el
código del backend:

| Job | Qué hace | Bloqueante |
|---|---|---|
| Lint | `ruff check` | Sí |
| Unit + integration + coverage | `pytest --cov` (umbral `fail_under = 80`) con un PostgreSQL real como service container | Sí |
| Build / import smoke | `python -m compileall` + import de los módulos principales | Sí |
| Secret scan | gitleaks (`--no-git`) | Sí |

No usa secrets del repositorio **ni actions de marketplace**: la política de la organización
sólo permite actions de repos propios, así que el checkout se hace con `git` + `github.token`
y Python sale del runner (ubuntu-latest trae Python 3.10+).

## Patrones de test reutilizables

- **Fuente de hexágonos fake**: `tests/conftest.py::FakeHexagonSource` + helper `hexrow()`.
- **API sin BD/Keycloak**: overrides de `get_db`/`get_hexagon_source` + `ensure_user` parcheado
  + autenticación por header interno (`X-Internal-Secret`/`X-User-Sub`) — ver `tests/integration/test_api.py`.
- **Guard de auth**: `_decode` parcheado y `ensure_user` a no-op — ver `tests/unit/test_keycloak_guard.py`.
- **Parquet de prueba**: se genera en `tmp_path` con `pyarrow` — ver `tests/unit/test_parquet_reader.py`.
- **PostgreSQL real**: `@pytest.mark.postgres` + fixtures `db_engine`/`db`/`session_factory` de
  `tests/integration/conftest.py`; el aislamiento entre tests se logra truncando las tablas en
  un fixture `autouse`. Para la ingesta completa (COPY y ORM): `tests/integration/test_ingest.py`.
