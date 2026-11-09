#!/usr/bin/env bash
# Corre una prueba k6 en Docker (no hace falta instalar k6).
#
#   ./perf/run.sh <journey|endpoints> [smoke|load|stress|spike|soak]
#
# Variables útiles (ver perf/README.md):
#   BASE_URL=http://localhost:8000   MODE=direct|bff|token   COOKIE='…'   TOKEN='…'
#   COOKIE_FILE=…    (storageState JSON de Playwright o un archivo "a=b; c=d")
#   TARGET_VUS=20    VUS=1 STEP=20s DURATION=5m
#   ENV_NAME=local|test|prod   STRICT=1 (aplica SLOs)   ALLOW_PROD_LOAD=1 (solo autorizado)
set -euo pipefail

TEST="${1:?uso: ./perf/run.sh <journey|endpoints> [smoke|load|stress|spike|soak]}"
PROFILE="${2:-smoke}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${MODE:-direct}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
STRICT="${STRICT:-0}"

# Entorno: si no se dice, se deduce del host. Producción = proyectovento.com.
if [ -z "${ENV_NAME:-}" ]; then
  if printf '%s' "$BASE_URL" | grep -qiE '(^https?://)?(www\.)?proyectovento\.com'; then
    ENV_NAME="prod"
  elif printf '%s' "$BASE_URL" | grep -qiE '(^https?://)?(test|staging)'; then
    ENV_NAME="test"
  else
    ENV_NAME="local"
  fi
fi

# Guarda dura: nunca load/stress/spike/soak contra producción sin autorización.
case "$PROFILE" in
  load|stress|spike|soak)
    if [ "$ENV_NAME" = "prod" ] && [ "${ALLOW_PROD_LOAD:-0}" != "1" ]; then
      echo "❌ PROFILE=$PROFILE contra producción bloqueado." >&2
      echo "   Usa smoke o pasa ALLOW_PROD_LOAD=1 con autorización explícita del equipo de operación." >&2
      exit 2
    fi
    ;;
esac

# En modo directo, k6 se autentica como el BFF con el secreto del .env del backend.
if [ "$MODE" = "direct" ] && [ -z "${SECRET:-}" ] && [ -f "$HERE/../.env" ]; then
  SECRET="$(grep '^INTERNAL_API_SECRET=' "$HERE/../.env" | cut -d= -f2- || true)"
fi

# Cookie: admite el storageState JSON de Playwright o un archivo de texto.
if [ -n "${COOKIE_FILE:-}" ] && [ -z "${COOKIE:-}" ]; then
  if [ ! -f "$COOKIE_FILE" ]; then
    echo "❌ COOKIE_FILE no existe: $COOKIE_FILE" >&2
    exit 2
  fi
  if head -c 1 "$COOKIE_FILE" | grep -q '{'; then
    COOKIE="$(python3 - "$COOKIE_FILE" "$BASE_URL" <<'PY'
import json, sys, urllib.parse
state = json.load(open(sys.argv[1]))
host = urllib.parse.urlparse(sys.argv[2]).hostname or ""
parts = []
for c in state.get("cookies", []):
    domain = (c.get("domain") or "").lstrip(".")
    if host == domain or host.endswith("." + domain):
        parts.append(f'{c["name"]}={c["value"]}')
print("; ".join(parts))
PY
)"
  else
    COOKIE="$(tr -d '\n' < "$COOKIE_FILE")"
  fi
fi

if [ "$MODE" = "bff" ] && [ -z "${COOKIE:-}" ]; then
  echo "❌ MODE=bff requiere COOKIE o COOKIE_FILE (sesión de un usuario real)." >&2
  exit 2
fi
if [ "$MODE" = "token" ] && [ -z "${TOKEN:-}" ]; then
  echo "❌ MODE=token requiere TOKEN (JWT de Keycloak)." >&2
  exit 2
fi

COMMIT="$(git -C "$HERE/.." rev-parse --short HEAD 2>/dev/null || true)"
mkdir -p "$HERE/results"

echo "▶ k6 $TEST · perfil=$PROFILE · entorno=$ENV_NAME · modo=$MODE · strict=$STRICT"
echo "  destino: $BASE_URL · commit: ${COMMIT:-—}"

exec docker run --rm -i --network host \
  -u "$(id -u):$(id -g)" \
  -v "$HERE:/perf" -w /perf \
  -e BASE_URL="$BASE_URL" \
  -e MODE="$MODE" -e SECRET="${SECRET:-}" -e COOKIE="${COOKIE:-}" -e TOKEN="${TOKEN:-}" \
  -e PROFILE="$PROFILE" \
  -e ENV_NAME="$ENV_NAME" -e STRICT="$STRICT" -e COMMIT="${COMMIT:-}" \
  -e ALLOW_PROD_LOAD="${ALLOW_PROD_LOAD:-0}" \
  -e TARGET_VUS="${TARGET_VUS:-20}" -e DURATION="${DURATION:-}" \
  -e VUS="${VUS:-1}" -e STEP="${STEP:-20s}" -e ITERATIONS="${ITERATIONS:-3}" \
  -e ENDPOINTS="${ENDPOINTS:-}" \
  "${K6_IMAGE:-grafana/k6:1.0.0}" run --quiet "k6/$TEST.js"
