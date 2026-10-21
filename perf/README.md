# Pruebas de performance (k6)

Suite **repetible** de pruebas de carga de la API del mapa. Sirve para medir una
**línea base**, verificar **SLOs** y comparar **antes/después** de cada optimización.

> `tools/loadtest.py` sigue siendo la herramienta de **diagnóstico** (EXPLAIN de cada
> consulta + salud de Postgres). Esta suite es la **medición estándar**.

## Conceptos en 1 minuto

| Término | Qué es |
|---|---|
| **VU** (usuario virtual) | Un "usuario" simulado que ejecuta el script en bucle. |
| **p95** | El 95 % de las peticiones tardó menos que esto. Se usa en vez del promedio porque el promedio esconde las peticiones lentas, que son las que nota el usuario. |
| **SLO / threshold** | Meta de rendimiento (ej. "p95 de `/area` < 1,5 s"). Si no se cumple, k6 termina con error ❌. |
| **Perfil** | Forma de la carga: `smoke` (1 usuario), `load` (carga esperada), `stress` (hasta romper), `spike` (pico brusco). |

## Requisitos

Solo Docker (k6 corre en el contenedor `grafana/k6:1.0.0`, no hay que instalarlo).

Para correr en local, el backend con datos:

```bash
docker compose up -d db
docker compose run --rm app python -m tools.ingest_parquet   # solo la 1ª vez (~2 min)
docker compose up -d app
```

## Uso

```bash
./perf/run.sh endpoints              # línea base: cada endpoint aislado, 1 usuario
VUS=10 ./perf/run.sh endpoints       # …y con 10 usuarios concurrentes por endpoint

./perf/run.sh journey smoke          # recorrido de usuario, 3 veces, 1 usuario
./perf/run.sh journey load           # 20 usuarios sostenidos 5 min (TARGET_VUS, DURATION)
./perf/run.sh journey stress         # escalones 10 → 25 → 50 → 100
./perf/run.sh journey spike          # pico de 4× TARGET_VUS y recuperación
```

Cada corrida deja en `perf/results/` (ignorado por git):
- `<fecha>_<test>_<perfil>.md`: tabla p50/p95/p99 por endpoint y por paso, para pegar en un PR o informe.
- `<fecha>_<test>_<perfil>.json`: todas las métricas.

### Contra otro entorno (test desplegado)

El frontend reexpone las mismas rutas (`/api/tiles/*`) a través del BFF, así que se
puede medir la ruta **completa** (navegador → Next → FastAPI → Postgres):

```bash
# 1) Inicia sesión en el entorno en el navegador.
# 2) DevTools → Network → cualquier petición a /api/tiles → copia el header "Cookie".
MODE=bff BASE_URL=https://<entorno-test> COOKIE='<cookie copiada>' ./perf/run.sh journey smoke
```

> ⚠️ **Nunca** corras `load`/`stress`/`spike` contra producción sin avisar al equipo:
> degrada el servicio para usuarios reales.

## Qué hace cada prueba

**`endpoints.js`: línea base.** Ejecuta cada endpoint aislado, uno tras otro (20 s
cada uno). La capa del mapa (`variables-bulk`) se mide por nivel de zoom
(`bulk_z6` … `bulk_z12`), porque su costo depende del área visible.

**`journey.js`: recorrido de usuario.** Simula lo que hace una persona en el mapa,
con pausas de "pensar" entre pasos:

1. **Abrir mapa**: las mismas peticiones que hace el navegador al cargar `/dashboard`
   (`health`, `config`, `excluded`, catálogo de variables ×4, `historico`). Se
   midieron con la suite de Playwright del frontend. **No pide capas.**
2. **Explorar**: activa 1 a 3 capas (`variables-bulk` del viewport) y hace 3 a 6
   paneos o zooms, cada uno con su propio `variables-bulk`.
3. **Detalle**: clic en un hexágono (`detail`).
4. **Informe**: selecciona entre 5 y 40 celdas res 7 (`area`).
5. **Histórico**: `historico`.

El generador de viewports (`lib/geo.js`) replica `zoomToH3Res` e `INITIAL_VIEW_STATE`
del frontend. Si esos cambian allá, hay que actualizarlos aquí.

## Frontend (navegador)

k6 mide el **servidor**. Lo que pasa **en el navegador** (render de deck.gl,
JavaScript que bloquea el hilo principal, FPS al hacer zoom) se mide con Playwright
en el repo del frontend: `AppVento/Vento/e2e-perf/` (ver su README). Las dos suites
se complementan. Por ejemplo, abrir el mapa tarda ~40 ms en el backend y varios
segundos en el navegador.

## SLOs

Definidos en `k6/journey.js` → `options.thresholds`. Hay dos orígenes:

- **NFR del PRD** (`AppVento/_bmad-output/planning-artifacts/prd.md`): metas de
  producto ya acordadas, medidas desde el punto de vista del usuario.
- **Propuesta:** metas por endpoint que todavía **no están definidas** en el proyecto.
  Hay que validarlas con el equipo.

| Qué | p95 objetivo | Origen |
|---|---|---|
| Paso "abrir mapa" completo | < 5 s | **NFR-P2** |
| `variables-bulk` (capa del mapa al cambiar variable o viewport) | < 2 s | **NFR-P3** |
| Paso "informe" (solo backend) | < 2 s | Propuesta (el PRD pide < 30 s para el informe completo, PDF incluido) |
| `area` | < 1,5 s | Propuesta |
| `variables`, `detail`, `historico` | < 300 ms | Propuesta |
| `warmup` | < 500 ms | Propuesta |
| `excluded` | < 1 s | Propuesta |
| Errores HTTP | < 1 % | Propuesta |

**Sin definir aún:** usuarios concurrentes objetivo (hoy `TARGET_VUS=20` por defecto),
disponibilidad (%) y presupuesto de errores. NFR-P4 (TTI < 3 s) y NFR-P1 (render sin
lag) son del navegador: se miden con Playwright, no con k6.

## Cómo comparar antes/después de un fix

1. En `develop` (antes): `./perf/run.sh endpoints` y `./perf/run.sh journey load`.
2. En la rama del fix: las mismas dos corridas, **en la misma máquina y con los mismos datos**.
3. Comparar los `.md` de `perf/results/` y pegar ambas tablas en el PR.
