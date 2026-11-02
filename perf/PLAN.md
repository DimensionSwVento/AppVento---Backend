# Plan de performance — backend

> Plan completo (frontend + backend + entornos + solicitud al encargado):
> `../AppVento/docs/testing/performance-testing-plan.md` en el workspace local.
> Este archivo resume lo específico del backend.

## Qué corre

| Prueba | Comando | Uso |
|---|---|---|
| Endpoints aislados | `./perf/run.sh endpoints` | línea base por endpoint |
| Recorrido | `./perf/run.sh journey smoke` | NFR-P2/P3, 1 usuario |
| Carga | `./perf/run.sh journey load` | 20 VUs, 5 min |
| Estrés | `./perf/run.sh journey stress` | techo (10→100 VUs) |
| Pico | `./perf/run.sh journey spike` | recuperación |
| Soak | `DURATION=30m ./perf/run.sh journey soak` | fugas/degradación |

`STRICT=1` aplica los SLOs como gate; sin él solo mide y reporta.

## Entornos

- **Local**: desarrollo y comparación relativa. No es cifra oficial.
- **Test**: cifra oficial; requiere URL, usuario de prueba y paridad con prod.
- **Producción**: **solo smoke de lectura** (`journey smoke`); `load/stress/spike/soak`
  bloqueados por `run.sh` salvo `ALLOW_PROD_LOAD=1` con autorización.

## SLOs

Una sola fuente: `perf/k6/lib/slo.js`. Incluye NFR-P2/P3 del PRD y propuestas por
endpoint/zoom. El reporte marca ✅/❌ con esos valores incluso en modo medición.

## Estado conocido (2026-10-20, local)

- `variables-bulk` z6 ≈ **48 s** p95; z8 ≈ 13 s; z10 ≈ 5,4 s; z12 ≈ 203 ms.
- `journey load` (20 VU): 50 % de errores; `variables-bulk` p95 ≈ 57 s.
- `area`, `detail`, `variables`, `excluded`, `warmup`: dentro de presupuesto.
- Causa: escaneo de ~1,6 M filas con JSONB ancho en `variables-bulk`. El caché
  en proceso (TTL corto) ayuda a requests repetidos; la solución estructural es
  preagregar por resolución al ingerir (Fase 3 del plan).

## Qué necesitamos para test

URL de test, usuario `perf-user`, secretos `PERF_USER`/`PERF_PASSWORD`,
`PERF_TEST_URL`, autorización de carga en test y monitoreo durante la ventana.
Detalle y mensaje listo: `../AppVento/docs/testing/performance-testing-ops-request.md`.

## CI

`.github/workflows/performance.yml` (manual + nightly):
1. checkout del frontend público,
2. login Playwright → cookie,
3. suite de navegador,
4. k6 por el BFF con esa cookie,
5. resumen en el job summary.
Se salta solo si `vars.PERF_TEST_URL` no existe. `strict=true` lo convierte en gate.
