# Pruebas con datos reales — Vento

Pasos para conectar el `.h3.parquet` real y probar el flujo completo (mapa,
informe, heatmap, histórico) contra el backend.

## 0. Prerrequisitos
- **Postgres** corriendo (para histórico + verificación de usuario).
- **Keycloak** (realm `vento`) — necesario para el login del frontend.
- El **parquet maestro** del equipo de investigación.

## 1. Colocar el parquet
Ruta canónica:
```
back_elements/data/master_variables.h3.parquet
```
(gitignored). Para otro nombre/ubicación: variable `PARQUET_PATH`.

## 2. Normalizar códigos → nombres
El parquet trae **códigos** (`TEC_001_sv001`, `REG_002`, `SOE_001`, `TEC_005`, …),
muchos `null`, y puede no traer `score_general`/`lat`/`lng`.

- Llenar `LABELS` en **`infrastructure/data/variable_catalog.py`** con el catálogo
  real (código base → nombre). Las **sub-variantes** `svNNN` se colapsan solas
  (`TEC_001_sv001` → `TEC_001`).
- Verificar que los prefijos de dimensión (`TEC/AMB/SOE/MAC/REG`) coinciden con
  el mapeo del frontend en `useAreaScores.ts` (`CAT_TO_DIM`) y
  `useInformeHeatmapLayer.ts` (`DIM_TO_CAT`).

> Si el catálogo aún no está, la UI funciona igual (fallback: el código como nombre).

## 3. Levantar el backend
```bash
cd back_elements
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # ajustar ↓
```
En `.env`:
- `DATABASE_URL` (Postgres),
- `KEYCLOAK_ISSUER`,
- `INTERNAL_API_SECRET` = **un secreto largo aleatorio** (debe ser IGUAL en el Vento),
- `PARQUET_PATH` (si no usas la ruta por defecto).

```bash
uvicorn main:app --reload --port 8000
```
Verificar:
```bash
curl -s localhost:8000/api/health      # → {"status":"ok","index_ready":true}
```
En los logs debe aparecer `Índice cargado: N hexágonos` y `DB lista`.

## 4. Levantar el frontend (Vento)
En `Vento/.env`:
- `BACKEND_URL=http://localhost:8000` (URL interna del FastAPI),
- `INTERNAL_API_SECRET` = **el mismo** del backend,
- config Keycloak (`AUTH_*`).

```bash
cd Vento && npm run dev
```
Toda la API va por el **BFF** (`/api/tiles/*`, `/api/historico`) — el Next reenvía
la identidad del usuario al backend con el secreto interno.

## 5. Probar E2E (con sesión iniciada)
- **Mapa:** hover un hex → popup de detalle (`/api/tiles/detail`).
- **Dimensiones:** el selector de variables carga (`/api/tiles/variables`).
- **Informe:** selecciona un área → los scores (barras + radar) y el **heatmap**
  se colorean con datos **reales** (`/api/tiles/area`). Si algún score falta → cae al placeholder.
- **Histórico:** Guardar → aparece en el listado; recargar → **persiste** (Postgres);
  filtros/búsqueda; borrar.

## 6. Probar solo el backend (sin frontend/Keycloak)
El histórico acepta el header interno directamente:
```bash
S=tu-INTERNAL_API_SECRET
curl -s localhost:8000/api/historico -H "X-Internal-Secret: $S" -H "X-User-Sub: test-user"
curl -s -X POST localhost:8000/api/tiles/area -H "X-Internal-Secret: $S" \
     -H "X-User-Sub: test-user" -H "Content-Type: application/json" \
     -d '{"hexes":["886602d903fffff"]}'
```
(La autenticación es obligatoria: en dev, autentícate con el header interno
`X-Internal-Secret` + `X-User-Sub` —como en el ejemplo de arriba— o con un JWT de
Keycloak. Solo `warmup`/`health` quedan abiertos.)

## 7. Apagar los placeholders (al validar)
Cuando todo venga del backend:
1. `USE_PLACEHOLDER_FALLBACK = false` en `Vento/src/app/Utils/projectData.ts`.
2. Verificar que la UI muestra **solo lo real** (sin backend, scores en 0 / heatmap neutro).
3. Eliminar los placeholders del **inventario** listado en `projectData.ts`.
