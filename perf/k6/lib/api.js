// Una función por endpoint. Cada petición lleva el tag `name` → k6 reporta
// latencias POR ENDPOINT y los umbrales (SLOs) se definen por ese tag.
import http from "k6/http";
import { check } from "k6";
import { BASE_URL, authHeaders } from "./config.js";

const JSON_HEADERS = { "Content-Type": "application/json" };

function get(path, name, auth = true) {
  const res = http.get(`${BASE_URL}${path}`, {
    headers: auth ? authHeaders() : {},
    tags: { name },
  });
  check(res, { [`${name} 200`]: (r) => r.status === 200 });
  return res;
}

function post(path, body, name) {
  const res = http.post(`${BASE_URL}${path}`, JSON.stringify(body), {
    headers: Object.assign({}, JSON_HEADERS, authHeaders()),
    tags: { name },
  });
  check(res, { [`${name} 200`]: (r) => r.status === 200 });
  return res;
}

export const health = () => get("/api/health", "health", false);
export const warmup = () => get("/api/tiles/warmup", "warmup", false);
export const catalogVariables = () => get("/api/admin/catalog/variables", "catalog-variables");
export const config = (key) => get(`/api/admin/catalog/config/${key}`, "catalog-config");
export const variables = () => get("/api/tiles/variables", "variables");
export const excluded = () => get("/api/tiles/excluded", "excluded");
export const detail = (h3) => get(`/api/tiles/detail/${h3}`, "detail");
export const historico = () => get("/api/historico", "historico");

export const variablesBulk = (codes, bbox, res) =>
  post("/api/tiles/variables-bulk", { codes, bbox, res }, "variables-bulk");

export const area = (hexes) => post("/api/tiles/area", { hexes }, "area");

/** Códigos de variable reales (de /variables). */
export function fetchCodes() {
  const res = variables();
  const vars = res.status === 200 ? res.json("variables") || [] : [];
  return vars.map((v) => v.name);
}

/** h3_index reales devueltos por variables-bulk (el bulk agrega a `res`). */
export function fetchCells(codes, bbox, res) {
  const r = variablesBulk(codes.slice(0, 1), bbox, res);
  if (r.status !== 200) return [];
  const data = r.json() || {};
  const rows = Object.values(data)[0] || [];
  return rows.map((row) => row.h3_index);
}
