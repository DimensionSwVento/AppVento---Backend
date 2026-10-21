// Resumen al terminar: texto en consola + JSON completo + tabla Markdown por
// endpoint/grupo en perf/results/ (para comparar corridas antes/después de un fix).
import { textSummary } from "https://jslib.k6.io/k6-summary/0.1.0/index.js";
import { BASE_URL, MODE, PROFILE } from "./config.js";

const fmt = (v) => (v === undefined ? "-" : v >= 100 ? v.toFixed(0) : v.toFixed(1));

function table(data) {
  const rows = [];
  for (const [key, m] of Object.entries(data.metrics)) {
    const sub = key.match(/^(http_req_duration|group_duration)\{(.+)\}$/);
    if (!sub || sub[2] === "expected_response:true") continue;
    const v = m.values;
    if (!v.max) continue; // el paso no llegó a ejecutarse
    const ok = m.thresholds ? Object.values(m.thresholds).every((t) => t.ok) : true;
    rows.push(`| ${sub[2]} | ${fmt(v.med)} | ${fmt(v["p(95)"])} | ${fmt(v["p(99)"])} | ${fmt(v.max)} | ${ok ? "✅" : "❌"} |`);
  }
  rows.sort();
  const reqs = data.metrics.http_reqs ? data.metrics.http_reqs.values : {};
  const failed = data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0;
  return [
    `- **Destino:** ${BASE_URL} (modo ${MODE}) · **perfil:** ${PROFILE}`,
    `- **Peticiones:** ${reqs.count || 0} (${fmt(reqs.rate || 0)} req/s) · **errores:** ${(failed * 100).toFixed(2)} %`,
    "",
    "| Métrica | p50 ms | p95 ms | p99 ms | máx ms | SLO |",
    "|---|---|---|---|---|---|",
    ...rows,
  ].join("\n");
}

export function summaryFor(testName) {
  return function handleSummary(data) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const base = `results/${stamp}_${testName}_${PROFILE}`;
    const md = `# ${testName} · ${PROFILE} · ${stamp}\n\n${table(data)}\n`;
    return {
      stdout: textSummary(data, { indent: " ", enableColors: true }) + "\n\n" + md,
      [`${base}.json`]: JSON.stringify(data, null, 2),
      [`${base}.md`]: md,
    };
  };
}
