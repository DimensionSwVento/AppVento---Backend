// SLOs de la suite de performance — UNA sola fuente para umbrales y reporte.
//
// Los `NFR-*` vienen del PRD (AppVento/_bmad-output/planning-artifacts/prd.md).
// Los demás son PROPUESTA del equipo de performance: se validan con el equipo y
// se ajustan solo con evidencia (no para "hacer pasar" una corrida).
//
// `STRICT=1` convierte estos presupuestos en thresholds de k6 (la corrida falla
// si no se cumplen). Sin STRICT la corrida solo MIDE: el reporte sigue marcando
// ✅/❌ contra el SLO, pero k6 termina con código 0 para poder guardar la línea
// base aunque el sistema esté por encima del presupuesto.

/** p95 por endpoint HTTP (ms). La clave es el tag `name` de la petición. */
export const SLO_HTTP = {
  health: 300,
  warmup: 500,
  "catalog-variables": 300,
  "catalog-config": 300,
  variables: 300,
  excluded: 1000,
  detail: 300,
  area: 1500,
  historico: 300,
  // NFR-P3: re-render al cambiar variable/filtro < 2 s.
  "variables-bulk": 2000,
};

/**
 * Presupuesto por zoom para la capa del mapa (bulk). El país completo (z6) es
 * un caso especial: el front pide una agregación a res4 de todo Colombia y el
 * backend debe resolverla sin escanear 1,6 M de filas por request. Mientras la
 * optimización estructural (preagregación) no esté, este presupuesto documenta
 * la meta de ingeniería; el NFR-P3 sigue siendo 2 s para la interacción real.
 */
export const SLO_BULK = {
  bulk_z12: 1000,
  bulk_z10: 1500,
  bulk_z8: 2000,
  bulk_z6: 3000,
};

/** Duración de un paso completo del recorrido (ms). */
export const SLO_STEP = {
  "abrir mapa": 5000, // NFR-P2
  explorar: 15000,
  detalle: 1000,
  informe: 2000,
  histórico: 1000,
};

/** p95 de `group_duration` (incluye pausas de "pensar"; informativo). */
export const SLO_GROUP = {
  "1. abrir mapa": 5000,
  "2. explorar": 15000,
  "3. detalle": 3000,
  "4. informe": 5000,
  "5. histórico": 3000,
};

/** SLO (ms) para una métrica del resumen, o undefined si no tiene presupuesto. */
export function sloForMetric(raw) {
  // "scenario:bulk_z6" | "name:variables" | "group:::1. abrir mapa" | "step:informe"
  if (raw.startsWith("group:::")) return SLO_GROUP[raw.slice("group:::".length)];
  if (raw.startsWith("step:")) return SLO_STEP[raw.slice("step:".length)];
  if (raw.startsWith("scenario:")) {
    const key = raw.slice("scenario:".length);
    return key in SLO_BULK ? SLO_BULK[key] : SLO_HTTP[key];
  }
  if (raw.startsWith("name:")) return SLO_HTTP[raw.slice("name:".length)];
  return undefined;
}

/** Thresholds de k6 para un endpoint HTTP, respetando STRICT. */
export function thresholdFor(raw, strict) {
  if (!strict) return ["max>=0"]; // solo reportar p95, no bloquear
  const slo = sloForMetric(raw);
  return slo ? [`p(95)<${slo}`] : ["max>=0"];
}

/** Thresholds de k6 para un paso (`step_ms`), respetando STRICT. */
export function thresholdForStep(step, strict) {
  if (!strict) return ["max>=0"];
  const slo = SLO_STEP[step];
  return slo ? [`p(95)<${slo}`] : ["max>=0"];
}
