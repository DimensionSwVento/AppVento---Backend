# Pruebas de performance (k6)

Suite **repetible** de pruebas de carga de la API del mapa. Sirve para medir una
**línea base**, verificar **SLOs** y comparar **antes/después** de cada optimización.

> Plan y entornos: [PLAN.md](PLAN.md).

> `tools/loadtest.py` sigue siendo la herramienta de **diagnóstico** (EXPLAIN de cada
> consulta + salud de Postgres). Esta suite es la **medición estándar**.

## Conceptos en 1 minuto

| Término | Qué es |
|---|---|
| **VU** (usuario virtual) | Un "usuario" simulado que ejecuta el script en bucle. |
| **p95** | El 95 % de las peticiones tardó menos que esto. Se usa en vez del promedio porque el promedio esconde las peticiones lentas, que son las que nota el usuario. |
| **SLO / threshold** | Meta de rendimiento (ej. "p95 de `/area` < 1,5 s"). Si no se cumple, el reporte lo marca ❌. |
| **Perfil** | Forma de la carga: `smoke` (1 usuario), `load` (carga esperada), `stress` (hasta romper), `spike` (pico brusco), `soak` (carga sostenida). |
| **Medición vs. gate** | Por defecto la corrida **solo mide** (k6 termina en 0 aunque haya SLOs en rojo). Con `STRICT=1` los SLOs son **thresholds** y la corrida falla; es lo que se usa como gate. |

## Requisitos

Solo Docker (k6 corre en el contenedor `grafana/k6:1.0.0`, no hay que instalarlo).
Para el modo `bff`/`token` y para exportar la cookie desde Playwright, Python 3.

Para correr en local, el backend con datos:

```bash
docker compose up -d db
docker compose run --rm app python -m tools.ingest_parquet   # solo la 1ª vez (~2 min)
docker compose up -d app
```

> La ingesta ya ejecuta `ANALYZE hexagon` al final (estadísticas frescas para el
> planner). Si la tabla se cargó con una versión anterior, corre una vez
> `VACUUM ANALYZE hexagon;` antes de medir; sin eso `excluded`/`variables-bulk`
> pueden tardar decenas de segundos por un plan malo (artefacto de medición).

## Uso

```bash
./perf/run.sh endpoints              # línea base: cada endpoint aislado, 1 usuario
ENDPOINTS=variables,detail,area ./perf/run.sh endpoints  # subconjunto (desarrollo)
STRICT=1 ./perf/run.sh endpoints     # gate: falla si no se cumple el SLO
VUS=10 ./perf/run.sh endpoints       # …y con 10 usuarios concurrentes por endpoint

./perf/run.sh journey smoke          # recorrido de usuario, 3 veces, 1 usuario
./perf/run.sh journey load           # 20 usuarios sostenidos 5 min (TARGET_VUS, DURATION)
DURATION=30m ./perf/run.sh journey soak
./perf/run.sh journey stress         # escalones 10 → 25 → 50 → 100
./perf/run.sh journey spike          # pico de 4× TARGET_VUS y recuperación
```

Cada corrida deja en `perf/results/` (ignorado por git):
- `<fecha>_<test>_<perfil>_<entorno>.md`: tabla p50/p95/p99 con SLO y estado ✅/❌, para pegar en un PR o informe.
- `<fecha>_<test>_<perfil>_<entorno>.json`: metadatos (entorno, commit, modo) + todas las métricas.

### Contra otro entorno (test desplegado)

El frontend reexpone las mismas rutas (`/api/tiles/*`) a través del BFF, así que se
puede medir la ruta **completa** (navegador → Next → FastAPI → Postgres):

```bash
# 1) Inicia sesión en el entorno con la suite de Playwright del frontend.
#    Eso deja e2e/.auth/perf.json y e2e/.auth/perf.cookie.
cd ../AppVento/Vento
PERF_BASE_URL=https://<entorno-test> PERF_USER=… PERF_PASSWORD=… npx playwright test \
  --config playwright.perf.config.ts --project=setup

# 2) k6 reutiliza esa cookie (acepta el storageState JSON de Playwright).
cd ../../AppVento---Backend
MODE=bff BASE_URL=https://<entorno-test> COOKIE_FILE=../AppVento/Vento/e2e/.auth/perf.json \
  ENV_NAME=test ./perf/run.sh journey load
```

También se puede copiar el header `Cookie` a mano desde DevTools → Network →
cualquier petición a `/api/tiles` (variable `COOKIE`). Para probar el backend
directo en una VPC, usa `MODE=direct` con `INTERNAL_API_SECRET` o `MODE=token`
con un JWT de Keycloak.

> ⚠️ **Nunca** corras `load`/`stress`/`spike`/`soak` contra producción sin avisar
> al equipo: degrada el servicio para usuarios reales. `run.sh` lo bloquea si
> detecta `proyectovento.com` o `ENV_NAME=prod`, salvo `ALLOW_PROD_LOAD=1`.
> Contra producción solo `smoke` de lectura.

## Qué hace cada prueba

**`endpoints.js`: línea base.** Ejecuta cada endpoint aislado, uno tras otro (20 s
cada uno). La capa del mapa (`variables-bulk`) se mide por nivel de zoom
(`bulk_z6` … `bulk_z12`), porque su costo depende del área visible.

**`journey.js`: recorrido de usuario.** Simula lo que hace una persona en el mapa,
con pausas de "pensar" entre pasos:

1. **Abrir mapa**: las mismas peticiones que hace el navegador al cargar `/dashboard`
   (`health`, `config`, `excluded`, catálogo de variables ×2, `historico`). No pide capas.
2. **Explorar**: activa 1 a 3 capas (`variables-bulk` del viewport) y hace 3 a 6
   paneos o zooms, cada uno con su propio `variables-bulk`.
3. **Detalle**: clic en un hexágono (`detail`).
4. **Informe**: selecciona entre 5 y 40 celdas res 7 (`area`).
5. **Histórico**: `historico`.

Además de `group_duration` (paso completo, incluye pausas), se reporta `step_ms`
(latencia pura de las peticiones del paso) para separar red/servidor de la
simulación humana.

El generador de viewports (`lib/geo.js`) replica `zoomToH3Res` e `INITIAL_VIEW_STATE`
del frontend. Si esos cambian allá, hay que actualizarlos aquí.

## SLOs

Definidos en **`k6/lib/slo.js`** (una sola fuente; el reporte los usa incluso en
modo medición). Hay dos orígenes:

- **NFR del PRD** (`AppVento/_bmad-output/planning-artifacts/prd.md`): metas de
  producto ya acordadas, medidas desde el punto de vista del usuario.
- **Propuesta:** metas por endpoint que todavía **no están definidas** en el proyecto.
  Hay que validarlas con el equipo.

| Qué | p95 objetivo | Origen |
|---|---|---|
| Paso "abrir mapa" completo | < 5 s | **NFR-P2** |
| `variables-bulk` (capa del mapa al cambiar variable o viewport) | < 2 s | **NFR-P3** |
| `bulk_z12` / `bulk_z10` / `bulk_z8` / `bulk_z6` | 1 / 1,5 / 2 / 3 s | Propuesta por zoom |
| Paso "informe" (solo backend) | < 2 s | Propuesta (el PRD pide < 30 s para el informe completo, PDF incluido) |
| `area` | < 1,5 s | Propuesta |
| `variables`, `detail`, `historico` | < 300 ms | Propuesta |
| `warmup` | < 500 ms | Propuesta |
| `excluded` | < 1 s | Propuesta |
| Errores HTTP | < 1 % | Propuesta |

**Sin definir aún:** usuarios concurrentes objetivo (hoy `TARGET_VUS=20` por defecto),
disponibilidad (%) y presupuesto de errores. NFR-P4 (TTI < 3 s) y NFR-P1 (render sin
lag) son del navegador: se miden con Playwright, no con k6.

## Frontend (navegador)

k6 mide el **servidor**. Lo que pasa **en el navegador** (render de deck.gl,
JavaScript que bloquea el hilo principal, FPS al hacer zoom) se mide con Playwright
en el repo del frontend: `AppVento/Vento/e2e-perf/` (ver su README). Las dos suites
se complementan. Por ejemplo, abrir el mapa tarda ~40 ms en el backend y varios
segundos en el navegador.

## Cómo comparar antes/después de un fix

1. En `develop` (antes): `./perf/run.sh endpoints` y `./perf/run.sh journey load`.
2. En la rama del fix: las mismas dos corridas, **en la misma máquina y con los mismos datos**.
3. Comparar los `.md` de `perf/results/` y pegar ambas tablas en el PR.

Para evitar conclusiones falsas: no mezclar corridas de `local`, `test` y `prod`
(otro hardware, otra BD, otra red) y no comparar una corrida con `STRICT=1` contra
una de medición (los tiempos son los mismos; el código de salida no).

## Guardas de seguridad

- `run.sh` deduce `ENV_NAME` del host y **bloquea** perfiles pesados contra producción
  salvo `ALLOW_PROD_LOAD=1`.
- Contra producción: solo `smoke` de lectura (health, login vía cookie, mapa, detalle).
- En test: `load`/`stress`/`spike`/`soak` solo en ventana acordada y avisando al equipo.
- La cookie de sesión caduca: expórtala de nuevo si la corrida dura más que la sesión.
- Los resultados locales **no** son la fuente para el informe oficial: la cifra oficial
  se toma del entorno de test con hardware comparable a producción.
