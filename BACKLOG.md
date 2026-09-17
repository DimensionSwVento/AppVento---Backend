# Backlog backend — Sprint 4 (features exclusivas de backend)

Lo que el frontend ya espera y falta en el backend. Origen: conversación de
cierre Sprint 4 + `../../Vento` (contratos) + `docs/spec/datos-requeridos.md`.

## 🔧 Código (buildable, sin esperar datos)

- [x] **`POST /api/tiles/variable`** — capa de una variable en **res8**:
      `{code, bbox?, threshold?, subtemplate?}` → `[{h3_index, value}]`. Reusa
      `fetch_bbox` + `variable_engine`. Casa con `mapLayerData.ts` (front manda
      `masterId` + `subtemplate`).
      · **Modo subtemplate**: si el `code` no se reconoce (aún no se sabe qué
        código de la máster es viento/densidad) y `subtemplate=true`, elige uno
        disponible AL AZAR DETERMINISTA (por el propio `code`) → datos reales de
        puente. Activable/desactivable por variable desde el front
        (`SUBTEMPLATE_VARS` en `mapLayerData.ts`).
      · Front: el heatmap de viento colorea el grid con el valor REAL por hex
        (cae al placeholder si no hay backend o el hex no está en el set res8).
- [x] **Actualizar la master** — `POST /api/admin/master/update` (inmediata o
      **programada** a una hora) + `GET /api/admin/master/status` (versión
      vigente, si hay ingesta en curso, próxima programada, y **advertencia**).
      Ingesta en hilo aparte + tick de fondo para las programadas. Cada versión
      queda en `master_version`; los **informes se estampan** con la versión
      vigente (`informe.master_version` → `masterVersion`). Migración `0004`.
      · Pendiente: que sea **no destructiva** (hoy reusa el ingest destructivo) y
        el **upload del parquet** desde la UI (hoy usa `PARQUET_PATH`/`source`).
      · Frontend pendiente: UI admin (programar/inmediato + confirmación con la
        advertencia) y mostrar la `masterVersion` en el informe.
- [x] **`tipo_celda` + offshore país en tiles** — `tipo_celda` ya viaja en el
      tile (`TileHex.t`). Añadido `TileHex.off` (mar/tierra 1/0) leído de la
      columna `OFFSHORE_VAR` (config, nombre real por definir).
- [x] **Fórmula AHP/WLC del score** — `get_area_report` usa
      `score_dim = Σ(peso·valor)/Σpeso` y `índice = Σ(peso_dim·score_dim)/Σpeso_dim`
      con pesos de `infrastructure/data/model_weights.py` (**placeholder = 1.0**,
      = media simple; rellenar con el AHP real). Respeta el `score_general` de la
      master si viene.
- [x] **Rol por usuario en `/api/admin/users`** — BFF Next trae los miembros del
      rol `admin` en 1 llamada (`getRealmRoleMembers`) y marca `roles`. Sin N+1.
- [x] **Seed del catálogo de variables** — `tools/gen_variables_seed.py` genera
      `variables_seed.json` (61+30) del catálogo del front; `catalog_seed` lo
      carga. Reemplazar por el catálogo real cuando llegue.

## 🗄️ Mover TODO dato no-template a BD (tabla propia, editable) — arquitectura

Principio (2026-09-11): nada de datos reales hardcodeado; cada conjunto en su
tabla, actualizable por admin. La master (parquet→`hexagon`) es solo UNA fuente.
Ya en BD: `catalog_variable`, `catalog_norma`, `catalog_escenario`, `master_version`.

Faltan tablas (hoy hardcodeado en front/back → mover a BD + CRUD + seed desde los
CSV reales + que el front las consuma vía seam):

- [ ] **Ampliar `catalog_variable`**: `master_id`, `criterio`, `subcriterio`,
      `map_kind` (area|heat|point), `map_threshold`, `aplica_en` (mar|tierra|ambos),
      `polaridad`. (Hoy en `variablesCatalog.ts` + `mapSymbols.ts`.)
- [ ] **`economic_series`** — PIB/IPC/precio-spot/demanda/oferta por (escenario,
      variable, año). Hoy: `economicScenarios.ts` (serie template).
- [ ] **`economic_incentive`** — impuestos/incentivos `{label, value}`. Hoy:
      `IMPUESTOS_CLAVE` en `Report.tsx`.
- [ ] **`regulatory_authority`** — `{sigla, nombre}`. Hoy: `REGULATORY_AUTHORITIES`.
- [ ] **`model_weight`** — pesos AHP por variable/dimensión (opcional: el modelo
      ya trae scores precomputados). Hoy: `model_weights.py`.
- [ ] **`capacity_range`** — área min/max por capacidad MW. Hoy: `areaRangeForCapacity`.
- [ ] **`turbine`** — specs S/M/L. Hoy: `AEROGENERADORES` en `projectData.ts`.
- [ ] **`data_source`** — fuente · versión · URL. Hoy: `REPORT_SOURCE_LINKS`.
- [ ] **`app_text`** — textos editables (disclaimer EAFIT…). Hoy: `reportTemplates.ts`.

Por cada tabla: modelo + migración + repo + service + router admin + **seed desde
el CSV real** + swap del front (fetch por seam, fallback template). Los VALORES
template siguen en el seam central tras `USE_BACKEND` (no romper el modo demo).

## 🔒 Ingesta de master SEGURA y sin bloquear la app (2026-09-11)

- [ ] **Backup previo**: antes de la ingesta destructiva, copiar la tabla vigente
      (`hexagon` + `hexagon_meta`) a una copia de respaldo (ej. `hexagon_backup`)
      que la ingesta NO toca → poder revertir si algo falla.
- [ ] **Swap por VISTA (zero-downtime)**: la app consulta una VISTA (ej.
      `hexagon` como vista → apunta a `hexagon_a`/`hexagon_b`). La ingesta carga
      en la tabla INACTIVA; al terminar, se re-apunta la vista (swap atómico) →
      la app nunca lee una tabla a medio cargar. Patrón blue-green de tablas.
      (Alternativa: cargar en `hexagon_new` y `RENAME` atómico dentro de una
      transacción.) Reemplaza el `replace_all` destructivo en caliente.
- [ ] `master_service` orquesta: backup → cargar en inactiva → recomputar
      `hexagon_meta`/anchors → swap de vista → marcar versión `ready`.

## 🔴 Insumo de datos (equipo, no código)

- [ ] `.h3.parquet` real ingerido (valores por hex, todas las dimensiones).
- [ ] Pesos AHP reales (magnitudes del modelo).
- [ ] Catálogo definitivo de variables (labels/fuentes/rangos/relevancia).
- [ ] Series económicas reales (PIB, IPC, precio spot, demanda, oferta por
      año/escenario) + escalado por capacidad + impuestos clave.
- [ ] Rangos área↔capacidad y specs de aerogenerador (D. Técnica).
- [ ] Formato de clave de variable (`TEC-004-T` vs `TEC_001_sv001`) + valor
      0–1 vs crudo.

## ✅ Hecho (Ola 1)

- [x] Admin catálogo: variables/normas/escenarios (CRUD + guard admin + seed de
      normas/escenarios + migración `0003`). Falta el seed rico de variables.
