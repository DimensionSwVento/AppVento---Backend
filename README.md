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
