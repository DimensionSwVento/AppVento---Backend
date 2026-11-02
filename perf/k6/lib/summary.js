// Resumen al terminar: texto en consola + JSON completo + tabla Markdown por
// endpoint/grupo en perf/results/ (para comparar corridas antes/después de un fix).
import { textSummary } from "https://jslib.k6.io/k6-summary/0.1.0/index.js";
import { metadata } from "./config.js";
import { sloForMetric } from "./slo.js";

const fmt = (v) => (v === undefined ? "-" : v >= 100 ? v.toFixed(0) : v.toFixed(1));

const METRIC_RE = /^(http_req_duration|group_duration|step_ms)\{(.+)\}$/;

function table(data) {
  const rows = [];
  for (const [metric, m] of Object.entries(data.metrics)) {
    const sub = metric.match(METRIC_RE);
    if (!sub || sub[2] === "expected_response:true") continue;
    const v = m.values;
    if (!v || !v.max) continue; // el paso no llegó a ejecutarse
    const slo = sloForMetric(sub[2]);
    const p95 = v["p(95)"];
    const ok = slo === undefined ? "—" : p95 <= slo ? "✅" : "❌";
    const budget = slo === undefined ? "—" : `${slo}`;
    rows.push(
      `| ${sub[2]} | ${fmt(v.med)} | ${fmt(p95)} | ${fmt(v["p(99)"])} | ${fmt(v.max)} | ${budget} | ${ok} |`,
    );
  }
  rows.sort();
  const reqs = data.metrics.http_reqs ? data.metrics.http_reqs.values : {};
  const failed = data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0;
  const vus = data.metrics.vus_max ? data.metrics.vus_max.values.max : undefined;
  const meta = metadata();
  return [
    `- **Destino:** ${meta.baseUrl} (modo ${meta.mode}) · **perfil:** ${meta.profile} · **entorno:** ${meta.env}`,
    `- **Código:** ${meta.commit || "sin commit"} · **modo:** ${meta.strict ? "GATE (STRICT=1)" : "medición"}`,
    `- **Peticiones:** ${reqs.count || 0} (${fmt(reqs.rate || 0)} req/s) · **errores:** ${(failed * 100).toFixed(2)} %` +
      (vus !== undefined ? ` · **VUs máx:** ${vus}` : ""),
    "",
    "| Métrica | p50 ms | p95 ms | p99 ms | máx ms | SLO ms | Estado |",
    "|---|---|---|---|---|---|---|",
    ...rows,
  ].join("\n");
}

export function summaryFor(testName) {
  return function handleSummary(data) {
    const meta = metadata();
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const base = `results/${stamp}_${testName}_${meta.profile}_${meta.env}`;
    const md = `# ${testName} · ${meta.profile} · ${meta.env} · ${stamp}\n\n${table(data)}\n`;
    return {
      stdout: textSummary(data, { indent: " ", enableColors: true }) + "\n\n" + md,
      [`${base}.json`]: JSON.stringify({ meta, summary: data }, null, 2),
      [`${base}.md`]: md,
    };
  };
}
