#!/usr/bin/env bash
# One-command startup for the presentation demo.
#
#   ./demo.sh          start backend + frontend (offline scripted provider)
#   ./demo.sh --live   use a real provider instead (needs HARNESS_API_KEY)
#   ./demo.sh --reset  wipe demo state (sessions/workspaces) and exit
#
# Backend:  http://localhost:8000     Frontend:  http://localhost:5173
# Log in as alice / alice123  (second browser: bob / bob123).
#
# Ctrl-C stops both servers.

set -euo pipefail
cd "$(dirname "$0")"

DEMO_DIR="${DEMO_DIR:-/tmp/harness-demo}"
export HARNESS_DB_URL="sqlite:///${DEMO_DIR}/demo.db"
export HARNESS_WORKSPACE_DIR="${DEMO_DIR}/ws"
export HARNESS_CONFINE_WORKSPACE=true          # the /etc/passwd block money shot
export HARNESS_JWT_SECRET_PATH="${DEMO_DIR}/jwt.key"

# Offline by default: the scripted DemoProvider runs the whole script with no
# API key. Pass --live to use a real provider (set HARNESS_API_KEY first).
export HARNESS_DEMO_FAKE=1

# --- flags -------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --reset)
      rm -rf "$DEMO_DIR"
      echo "Demo state wiped ($DEMO_DIR). alice/bob will be re-seeded on next start."
      exit 0 ;;
    --live)
      unset HARNESS_DEMO_FAKE
      if [ -z "${HARNESS_API_KEY:-}" ]; then
        echo "warning: --live set but HARNESS_API_KEY is empty; real models will fail." >&2
      fi ;;
  esac
done

mkdir -p "$DEMO_DIR"

# --- dependency preflight ----------------------------------------------------
python3 -c "import fastapi, uvicorn, jwt, sqlalchemy" 2>/dev/null || {
  echo "Installing backend deps (fastapi/uvicorn/PyJWT/sqlalchemy)…"
  pip install -q -r requirements.txt -r requirements-server.txt PyJWT
}

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend deps (npm install)…"
  (cd frontend && npm install)
fi

# --- launch ------------------------------------------------------------------
PIDS=()
cleanup() {
  echo; echo "Stopping demo…"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo "Starting backend on http://localhost:8000  (mode: ${HARNESS_DEMO_FAKE:+offline scripted}${HARNESS_DEMO_FAKE:-live provider})"
python3 -m server.app &
PIDS+=($!)

echo "Starting frontend on http://localhost:5173"
(cd frontend && npm run dev) &
PIDS+=($!)

sleep 3
echo
echo "─────────────────────────────────────────────"
echo "  Demo ready:  http://localhost:5173"
echo "  Log in as    alice / alice123"
echo "  2nd browser  bob / bob123"
echo "  Ctrl-C to stop."
echo "─────────────────────────────────────────────"

wait
