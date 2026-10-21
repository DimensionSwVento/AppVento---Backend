// Configuración común de la suite k6 (todo por variables de entorno, ver perf/README.md).
//
//   BASE_URL  → a quién se le pega. Backend directo (http://localhost:8000) o el
//               frontend/BFF (https://test.…): las rutas /api/tiles/* son iguales.
//   MODE      → "direct": se autentica como el BFF (X-Internal-Secret + X-User-Sub).
//               "bff":    usa la cookie de sesión de NextAuth de un usuario real.
//   SECRET    → INTERNAL_API_SECRET del backend (solo MODE=direct; run.sh lo lee del .env).
//   COOKIE    → header Cookie completo de una sesión del navegador (solo MODE=bff).
//   PROFILE   → smoke | load | stress | spike (ver profiles.js).

export const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/$/, "");
export const MODE = __ENV.MODE || "direct";
export const PROFILE = __ENV.PROFILE || "smoke";

const SECRET = __ENV.SECRET || "";
const COOKIE = __ENV.COOKIE || "";

if (MODE === "direct" && !SECRET) {
  throw new Error("MODE=direct requiere SECRET (INTERNAL_API_SECRET del backend).");
}
if (MODE === "bff" && !COOKIE) {
  throw new Error("MODE=bff requiere COOKIE (copiada del navegador con sesión iniciada).");
}

/** Headers de autenticación. En modo directo cada VU es un usuario distinto. */
export function authHeaders() {
  if (MODE === "bff") return { Cookie: COOKIE };
  return {
    "X-Internal-Secret": SECRET,
    "X-User-Sub": `perf-k6-vu${__VU}`,
    "X-User-Roles": "user",
  };
}
