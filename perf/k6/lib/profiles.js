// Perfiles de carga: CUÁNTOS usuarios virtuales (VUs) y CÓMO cambian en el tiempo.
//
//   smoke  → 1 usuario, ITERATIONS recorridos completos (def. 3). ¿El script funciona?
//            + latencia sin concurrencia (línea base).
//   load   → la carga esperada (TARGET_VUS, def. 20) sostenida. ¿Cumplimos los SLOs?
//   stress → sube por escalones hasta pasar el techo. ¿Dónde y cómo se rompe?
//   spike  → salto brusco de 0 a muchos. ¿Se recupera después?

const TARGET = parseInt(__ENV.TARGET_VUS || "20", 10);

export function scenarioFor(profile, exec) {
  switch (profile) {
    case "smoke":
      return {
        executor: "per-vu-iterations",
        exec,
        vus: 1,
        iterations: parseInt(__ENV.ITERATIONS || "3", 10),
        maxDuration: "15m",
      };
    case "load":
      return {
        executor: "ramping-vus",
        exec,
        startVUs: 0,
        stages: [
          { duration: "1m", target: TARGET },  // rampa de subida
          { duration: __ENV.DURATION || "5m", target: TARGET }, // meseta
          { duration: "30s", target: 0 },      // bajada
        ],
        gracefulRampDown: "30s",
      };
    case "stress":
      return {
        executor: "ramping-vus",
        exec,
        startVUs: 0,
        stages: [
          { duration: "1m", target: 10 }, { duration: "2m", target: 10 },
          { duration: "1m", target: 25 }, { duration: "2m", target: 25 },
          { duration: "1m", target: 50 }, { duration: "2m", target: 50 },
          { duration: "1m", target: 100 }, { duration: "2m", target: 100 },
          { duration: "1m", target: 0 },
        ],
        gracefulRampDown: "30s",
      };
    case "spike":
      return {
        executor: "ramping-vus",
        exec,
        startVUs: 0,
        stages: [
          { duration: "1m", target: 5 },        // tráfico normal
          { duration: "10s", target: TARGET * 4 }, // pico
          { duration: "1m", target: TARGET * 4 },
          { duration: "10s", target: 5 },        // vuelve a la normalidad…
          { duration: "2m", target: 5 },         // …¿se recuperó?
          { duration: "10s", target: 0 },
        ],
        gracefulRampDown: "30s",
      };
    default:
      throw new Error(`PROFILE desconocido: ${profile} (smoke|load|stress|spike)`);
  }
}
