// Configuración común de la suite k6 (todo por variables de entorno, ver perf/README.md).
//
//   BASE_URL   → a quién se le pega. Backend directo (http://localhost:8000) o el
//                frontend/BFF (https://test.…): las rutas /api/tiles/* son iguales.
//   MODE       → "direct": se autentica como el BFF (X-Internal-Secret + X-User-Sub).
//                "bff":    usa la cookie de sesión de NextAuth de un usuario real.
//                "token":  usa un JWT de Keycloak (Authorization: Bearer …).
//   SECRET     → INTERNAL_API_SECRET del backend (solo MODE=direct; run.sh lo lee del .env).
//   COOKIE     → header Cookie completo de una sesión del navegador (solo MODE=bff).
//   TOKEN      → JWT de Keycloak (solo MODE=token).
//   PROFILE    → smoke | load | stress | spike | soak (ver profiles.js).
//   ENV_NAME   → local | test | prod (metadato del reporte y guarda de seguridad).
//   STRICT     → "1" aplica los SLOs como thresholds (la corrida falla si no cumple).
//   COMMIT     → hash corto del código bajo prueba (lo inyecta run.sh).

export const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/$/, "");
export const MODE = __ENV.MODE || "direct";
export const PROFILE = __ENV.PROFILE || "smoke";
export const ENV_NAME = __ENV.ENV_NAME || "local";
export const STRICT = __ENV.STRICT === "1";
export const COMMIT = __ENV.COMMIT || "";

const SECRET = __ENV.SECRET || "";
const COOKIE = __ENV.COOKIE || "";
const TOKEN = __ENV.TOKEN || "";

if (MODE === "direct" && !SECRET) {
  throw new Error("MODE=direct requiere SECRET (INTERNAL_API_SECRET del backend).");
}
if (MODE === "bff" && !COOKIE) {
  throw new Error("MODE=bff requiere COOKIE (copiada del navegador con sesión iniciada).");
}
if (MODE === "token" && !TOKEN) {
  throw new Error("MODE=token requiere TOKEN (JWT de Keycloak).");
}

// Guarda dura: nunca cargar/stress/spike contra producción sin autorización explícita.
// El `run.sh` también lo valida antes de arrancar el contenedor; esto cubre a quien
// invoque k6 directamente.
const host = BASE_URL.replace(/^https?:\/\//i, "").split("/")[0].split(":")[0];
const isProd = /(^|\.)proyectovento\.com$/i.test(host);
const heavy = ["load", "stress", "spike", "soak"].includes(PROFILE);
if ((isProd || ENV_NAME === "prod") && heavy && __ENV.ALLOW_PROD_LOAD !== "1") {
  throw new Error(
    `PROFILE=${PROFILE} contra producción (${BASE_URL}) bloqueado. ` +
      "Usa smoke o pasa ALLOW_PROD_LOAD=1 con autorización explícita del equipo de operación.",
  );
}

/** Headers de autenticación. En modo directo cada VU es un usuario distinto. */
export function authHeaders() {
  if (MODE === "bff") return { Cookie: COOKIE };
  if (MODE === "token") return { Authorization: `Bearer ${TOKEN}` };
  return {
    "X-Internal-Secret": SECRET,
    "X-User-Sub": `perf-k6-vu${__VU}`,
    "X-User-Roles": "user",
  };
}

/** Metadatos que el resumen escribe en el JSON/Markdown. */
export function metadata() {
  return { baseUrl: BASE_URL, mode: MODE, profile: PROFILE, env: ENV_NAME, strict: STRICT, commit: COMMIT };
}
