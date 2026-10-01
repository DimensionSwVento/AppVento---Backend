# Vento — Backend (FastAPI)

Backend del proyecto Vento, reconstruido en **FastAPI** con **Clean Architecture / DDD**.

- **Autenticación (toda la API):** **guard dual** `require_user` — acepta el **JWT de Keycloak** (RS256 vía JWKS) **o** el **header interno del BFF** (`X-Internal-Secret` + `X-User-Sub`/`X-User-Roles`) cuando el frontend Next reenvía la identidad ya verificada (backend privado en AccuWeb). No hay login/registro (Keycloak). En ambos casos verifica/provisiona el usuario en Postgres y decide el permiso por roles.
- **Mapa:** sirve hexágonos por tile y — método principal del informe — **por ÁREA**: recibe el polígono del área seleccionada y devuelve solo los hexágonos de esa área con su **score general, scores por dimensión y detalle de variables**.
- **Histórico:** persiste los informes **por usuario** en **Postgres** (se guardan los parámetros compactados, nunca el PDF).

## Estructura

```
back_elements/
├── main.py                         # App FastAPI (CORS, warmup, routers, lifespan)
├── shared/config.py                # Config por env (parquet, DB, Keycloak, CORS)
├── domain/                         # Núcleo puro (sin FastAPI ni DB)
│   ├── models/hexagon.py           #   HexagonData, TileHex, HexDetail, AreaHex
│   └── query_engine/               #   engine + spatial_resolver + aggregator + variable_engine
├── application/services/           # Orquestación (tile_service, historial_service)
├── infrastructure/
│   ├── spatial/tile_index.py       #   Índice H3 en memoria
│   ├── data/parquet_reader.py      #   Lectura del .h3.parquet
│   ├── auth/keycloak_guard.py      #   Verificación JWT + roles + usuario en BD
│   └── db/                         #   session + models (ORM) + repository
└── api/routers/                    # tiles.py · historial.py
```

## Endpoints (bajo `/api`)

| Método | Ruta | Descripción | Guard |
|--------|------|-------------|-------|
| GET  | `/api/health` | Estado + índice listo | — |
| GET  | `/api/tiles/warmup` | Carga el parquet en memoria | — |
| GET  | `/api/tiles/{z}/{x}/{y}` | Hexes de un tile | JWT |
| GET  | `/api/tiles/detail/{h3}` | Detalle de un hex | JWT |
| GET  | `/api/tiles/variables` | Variables disponibles | JWT |
| **POST** | **`/api/tiles/area`** | **Polígono/hexes → hexágonos del área con scores por dimensión + detalle** | JWT |
| GET    | `/api/historico` | Informes del usuario | JWT |
| POST   | `/api/historico` | Guardar informe | JWT |
| DELETE | `/api/historico/{id}` | Eliminar informe propio | JWT |

`POST /api/tiles/area` — body:
```json
{ "polygon": [[lng, lat], ...] }        // o  { "hexes": ["87...", ...] }
```
respuesta: `[{ "h3_index", "lat", "lng", "score_general", "scores": {"TEC":0.8,...}, "tipo_celda", "variables": {...} }]`

## Correr en local

```bash
cd back_elements
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # ajustar DATABASE_URL / KEYCLOAK_ISSUER / PARQUET_PATH
uvicorn main:app --reload --port 8000
```

Requiere **Postgres** (para el histórico y la verificación de usuario) y **Keycloak** (realm `vento`). La **autenticación es obligatoria** (no configurable): todos los endpoints de datos exigen identidad verificada — JWT de Keycloak o el header interno del BFF (`X-Internal-Secret` + `X-User-Sub`/`X-User-Roles`) con el `INTERNAL_API_SECRET`. Solo `warmup`/`health` quedan abiertos.

## Datos reales

- Parquet maestro → **`data/master_variables.h3.parquet`** (ver [data/README.md](data/README.md)).
- Normalizar códigos → nombres en [`infrastructure/data/variable_catalog.py`](infrastructure/data/variable_catalog.py).
- Pasos completos de prueba: **[PRUEBAS-DATOS-REALES.md](PRUEBAS-DATOS-REALES.md)**.

> Migraciones: en dev las tablas se crean solas al arrancar (`init_db`). En prod usar Alembic.

## Límites de concurrencia por endpoint (capacidad)

Medido con `tools/loadtest.py` en el server de **2 workers / ~2 GB**. Techo =
usuarios concurrentes antes de degradar (err>25% o p95>8 s). "Por GB" ≈ 1 worker ≈
18 operaciones de BD concurrentes por cada ~1 GB de RAM (escala con workers).

| Endpoint | Techo (2 GB) | ≈ por GB | Cuello | Datos movidos / request |
|---|---|---|---|---|
| `GET /tiles/detail` | ~100 | ~50 | app (2 workers) | 1 hex · 1 fila (~0,7 KB) · PK |
| `POST /tiles/area` | ~100 | ~50 | app | 20–300 hexes (res8) · ~0,06–0,22 MB · PK/IN |
| `GET /tiles/variables` | ~100 | ~50 | app | 62 filas meta (~KB) |
| `GET /tiles/warmup` | ~50 → **>100** (fix) | ~25 → >50 | BD (`count(*)`) | estimado `reltuples` (0 filas; antes ~954 MB) |
| `GET /tiles/excluded` | scan ~30 s → ms (índice+caché) | — | BD (sin índice) | solo `h3_index` excluidos |
| `POST /tiles/variables-bulk` | **~10** | ~5 | **BD** (scan bbox) | hasta ~221 K filas × ~0,7 KB ≈ **165 MB leídos** |

Detalle, herramientas y configuración: **[docs/ops/documento-tecnico-comportamiento-herramientas-config.md](../../docs/ops/documento-tecnico-comportamiento-herramientas-config.md)** ·
evidencia: **[docs/testing/benchmark-carga-h3-2026-09-30.md](../../docs/testing/benchmark-carga-h3-2026-09-30.md)**.
