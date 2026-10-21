#!/usr/bin/env bash
# Corre una prueba k6 en Docker (no hace falta instalar k6).
#
#   ./perf/run.sh <journey|endpoints> [smoke|load|stress|spike]
#
# Variables útiles (ver perf/README.md):
#   BASE_URL=http://localhost:8000   MODE=direct|bff   COOKIE='…'   TARGET_VUS=20
#   VUS=1 STEP=20s (solo endpoints)   DURATION=5m
set -euo pipefail

TEST="${1:?uso: ./perf/run.sh <journey|endpoints> [smoke|load|stress|spike]}"
PROFILE="${2:-smoke}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${MODE:-direct}"

# En modo directo, k6 se autentica como el BFF con el secreto del .env del backend.
if [ "$MODE" = "direct" ] && [ -z "${SECRET:-}" ] && [ -f "$HERE/../.env" ]; then
  SECRET="$(grep '^INTERNAL_API_SECRET=' "$HERE/../.env" | cut -d= -f2- || true)"
fi

mkdir -p "$HERE/results"
exec docker run --rm -i --network host \
  -u "$(id -u):$(id -g)" \
  -v "$HERE:/perf" -w /perf \
  -e BASE_URL="${BASE_URL:-http://localhost:8000}" \
  -e MODE="$MODE" -e SECRET="${SECRET:-}" -e COOKIE="${COOKIE:-}" \
  -e PROFILE="$PROFILE" \
  -e TARGET_VUS="${TARGET_VUS:-20}" -e DURATION="${DURATION:-}" \
  -e VUS="${VUS:-1}" -e STEP="${STEP:-20s}" -e ITERATIONS="${ITERATIONS:-3}" \
  grafana/k6:1.0.0 run --quiet "k6/$TEST.js"
