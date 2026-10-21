// LÍNEA BASE POR ENDPOINT: cada endpoint se prueba AISLADO, uno tras otro, para
// saber cuánto cuesta cada uno sin que los demás interfieran.
//
// Por defecto 1 usuario (latencia pura, sin concurrencia). Con VUS=10 se ve cómo
// se degrada cada endpoint con concurrencia. La capa del mapa (variables-bulk) se
// mide por nivel de zoom porque su costo depende del área visible.
//
//   ./perf/run.sh endpoints              (VUS=1, 20s por endpoint)
//   VUS=10 ./perf/run.sh endpoints
import { sleep } from "k6";
import * as api from "./lib/api.js";
import { viewportBBox, zoomToH3Res, randomCenter, INITIAL_VIEW } from "./lib/geo.js";
import { summaryFor } from "./lib/summary.js";

const VUS = parseInt(__ENV.VUS || "1", 10);
const STEP = __ENV.STEP || "20s";
const STEP_S = parseInt(STEP, 10);

const ENDPOINTS = [
  "warmup", "variables", "excluded", "detail", "area", "historico",
  "bulk_z12", "bulk_z10", "bulk_z8", "bulk_z6", // z6 (país completo) al final: es el más lento
];

const scenarios = {};
const thresholds = { http_req_failed: ["rate<0.01"] };
ENDPOINTS.forEach((name, i) => {
  scenarios[name] = {
    executor: "constant-vus",
    exec: name,
    vus: VUS,
    duration: STEP,
    gracefulStop: "2m", // deja terminar la última petición lenta (sin cortarla a medias)
    startTime: `${i * (STEP_S + 5)}s`, // secuenciales, con 5 s de respiro entre cada uno
  };
  // Umbral "siempre verdadero": solo para que k6 reporte p95 por escenario.
  thresholds[`http_req_duration{scenario:${name}}`] = ["max>=0"];
});

export const options = { scenarios, thresholds, summaryTrendStats: ["med", "p(95)", "p(99)", "max"] };

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

export function setup() {
  const codes = api.fetchCodes();
  const cells7 = [];
  const cells8 = [];
  for (let i = 0; i < 5; i++) {
    const c = randomCenter();
    cells7.push(...api.fetchCells(codes, viewportBBox(c.lng, c.lat, 10), 7));
    cells8.push(...api.fetchCells(codes, viewportBBox(c.lng, c.lat, 12), 8));
  }
  return { codes, cells7, cells8 };
}

function bulkAtZoom(codes, zoom) {
  const c = zoom === INITIAL_VIEW.zoom ? INITIAL_VIEW : randomCenter();
  api.variablesBulk(codes.slice(0, 2), viewportBBox(c.lng, c.lat, zoom), zoomToH3Res(zoom));
  sleep(0.5);
}

export function warmup() { api.warmup(); sleep(0.5); }
export function variables() { api.variables(); sleep(0.5); }
export function excluded() { api.excluded(); sleep(0.5); }
export function historico() { api.historico(); sleep(0.5); }
export function detail(d) { api.detail(pick(d.cells8)); sleep(0.5); }
export function area(d) {
  const n = 5 + Math.floor(Math.random() * 36);
  const start = Math.floor(Math.random() * Math.max(1, d.cells7.length - n));
  api.area(d.cells7.slice(start, start + n));
  sleep(0.5);
}
export function bulk_z6(d) { bulkAtZoom(d.codes, 6); }
export function bulk_z8(d) { bulkAtZoom(d.codes, 8); }
export function bulk_z10(d) { bulkAtZoom(d.codes, 10); }
export function bulk_z12(d) { bulkAtZoom(d.codes, 12); }

export const handleSummary = summaryFor("endpoints");
