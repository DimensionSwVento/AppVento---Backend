// RECORRIDO DE USUARIO: lo que hace una persona real en el mapa de Vento.
//
//   1. Abrir mapa   → las peticiones que hace el navegador al cargar /dashboard
//                     (medidas con e2e-perf/map.perf.ts del front: health, config,
//                     excluded, catálogo de variables ×4, histórico). NO pide capas.
//   2. Explorar     → activa capas y hace paneos/zooms; cada vista pide variables-bulk
//   3. Detalle      → clic en un hexágono
//   4. Informe      → selecciona un área (celdas res 7) y pide /area
//   5. Histórico    → abre sus informes guardados
//
// Entre pasos hay "tiempo de pensar" (sleep), como un humano. Cada paso es un
// `group` → k6 mide cuánto tarda el paso COMPLETO (lo que siente el usuario).
// Además se mide `step_ms` SIN las pausas de pensar, para separar latencia real
// de la simulación humana.
//
//   ./perf/run.sh journey smoke|load|stress|spike|soak
//   STRICT=1 ./perf/run.sh journey load
import { group, sleep } from "k6";
import { Trend } from "k6/metrics";
import * as api from "./lib/api.js";
import { PROFILE, STRICT } from "./lib/config.js";
import { scenarioFor } from "./lib/profiles.js";
import { INITIAL_VIEW, viewportBBox, zoomToH3Res, randomCenter, pan } from "./lib/geo.js";
import { summaryFor } from "./lib/summary.js";
import { thresholdFor, thresholdForStep } from "./lib/slo.js";

const stepMs = new Trend("step_ms", true);

// Umbrales: los que tienen NFR-* vienen del PRD (AppVento/_bmad-output/planning-artifacts/prd.md);
// los demás son PROPUESTA del equipo de performance (ver perf/README.md → SLOs).
// En modo medición (STRICT=0) solo se reportan; en modo gate se aplican.
const thresholds = {
  http_req_failed: ["rate<0.01"],
  "http_req_duration{name:health}": thresholdFor("name:health", STRICT),
  "http_req_duration{name:warmup}": thresholdFor("name:warmup", STRICT),
  "http_req_duration{name:catalog-variables}": thresholdFor("name:catalog-variables", STRICT),
  "http_req_duration{name:catalog-config}": thresholdFor("name:catalog-config", STRICT),
  "http_req_duration{name:variables}": thresholdFor("name:variables", STRICT),
  "http_req_duration{name:excluded}": thresholdFor("name:excluded", STRICT),
  "http_req_duration{name:variables-bulk}": thresholdFor("name:variables-bulk", STRICT),
  "http_req_duration{name:detail}": thresholdFor("name:detail", STRICT),
  "http_req_duration{name:area}": thresholdFor("name:area", STRICT),
  "http_req_duration{name:historico}": thresholdFor("name:historico", STRICT),
  "step_ms{step:abrir mapa}": thresholdForStep("abrir mapa", STRICT),
  "step_ms{step:detalle}": thresholdForStep("detalle", STRICT),
  "step_ms{step:informe}": thresholdForStep("informe", STRICT),
  "step_ms{step:histórico}": thresholdForStep("histórico", STRICT),
};

export const options = {
  scenarios: { journey: scenarioFor(PROFILE, "journey") },
  summaryTrendStats: ["med", "p(95)", "p(99)", "max"],
  thresholds,
  // El muestreo del mapa (setup) puede tardar en una BD fría; no debe abortar
  // la corrida antes de medir. No afecta la duración de los escenarios.
  setupTimeout: "5m",
};

const think = (min, max) => sleep(min + Math.random() * (max - min));
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function sample(arr, n) {
  const start = Math.floor(Math.random() * Math.max(1, arr.length - n));
  return arr.slice(start, start + n); // tramo contiguo ≈ celdas vecinas de la consulta
}

function timedStep(name, fn) {
  const t0 = Date.now();
  const out = fn();
  stepMs.add(Date.now() - t0, { step: name });
  return out;
}

// Una vez antes de la prueba: códigos de variable y celdas reales para clics/áreas.
export function setup() {
  const codes = api.fetchCodes();
  if (codes.length === 0) throw new Error("/tiles/variables no devolvió variables (¿BD vacía? ¿auth?)");

  const cells7 = [];
  const cells8 = [];
  for (let i = 0; i < 5; i++) {
    const c = randomCenter();
    cells7.push(...api.fetchCells(codes, viewportBBox(c.lng, c.lat, 10), 7));
    cells8.push(...api.fetchCells(codes, viewportBBox(c.lng, c.lat, 12), 8));
  }
  if (cells7.length === 0 || cells8.length === 0) {
    throw new Error("No se obtuvieron celdas de muestra de variables-bulk.");
  }
  console.log(`setup: ${codes.length} variables · ${cells7.length} celdas res7 · ${cells8.length} celdas res8`);
  return { codes, cells7, cells8 };
}

export function journey({ codes, cells7, cells8 }) {
  // Capas activas del usuario (1–3 variables).
  const layers = sample(codes, 1 + Math.floor(Math.random() * Math.min(3, codes.length)));
  let view = Object.assign({}, INITIAL_VIEW);

  group("1. abrir mapa", () => {
    timedStep("abrir mapa", () => {
      api.health();
      api.config("support_email");
      api.excluded();
      for (let i = 0; i < 2; i++) api.catalogVariables(); // modelo + informativa (el front las cachea)
      api.historico();
    });
  });
  think(2, 5);

  group("2. explorar", () => {
    // Activa las capas con la vista actual y luego navega.
    api.variablesBulk(layers, viewportBBox(view.lng, view.lat, view.zoom), zoomToH3Res(view.zoom));
    think(1, 3);
    const moves = 3 + Math.floor(Math.random() * 4);
    for (let i = 0; i < moves; i++) {
      const r = Math.random();
      if (r < 0.6 && view.zoom < 12) {
        const c = i === 0 ? randomCenter() : view; // primer zoom: va hacia una zona de interés
        view = { lng: c.lng, lat: c.lat, zoom: Math.min(12, view.zoom + 1 + Math.floor(Math.random() * 2)) };
      } else if (r < 0.85) {
        view = pan(view);
      } else {
        view = Object.assign({}, view, { zoom: Math.max(6, view.zoom - 1) });
      }
      api.variablesBulk(layers, viewportBBox(view.lng, view.lat, view.zoom), zoomToH3Res(view.zoom));
      think(1, 3);
    }
  });

  group("3. detalle", () => {
    timedStep("detalle", () => api.detail(pick(cells8)));
  });
  think(2, 4);

  group("4. informe", () => {
    timedStep("informe", () => api.area(sample(cells7, 5 + Math.floor(Math.random() * 36))));
  });
  think(3, 6);

  group("5. histórico", () => {
    timedStep("histórico", () => api.historico());
  });
  think(2, 4);
}

export const handleSummary = summaryFor("journey");
