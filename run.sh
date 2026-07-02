#!/usr/bin/env bash
# MrFoX-MeM — dev convenience launcher (macOS / zsh-friendly bash).
#
# Ensures the venv exists, starts the core API in the background bound to
# 127.0.0.1, waits for /health, opens the UI in the default browser, then
# tails the server log. Ctrl-C cleans up the background server.
#
# Usage: ./run.sh
set -euo pipefail

# Resolve the project root (this script's directory), regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="127.0.0.1"
PORT="8077"
BASE="http://${HOST}:${PORT}"
VENV=".venv"
LOG_DIR="data"
LOG_FILE="${LOG_DIR}/server.log"
SERVER_PID=""

log()  { printf '\033[1;36m[mrfox]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[mrfox]\033[0m %s\n' "$*" >&2; }

cleanup() {
  # Trap handler: stop the background server if we started it.
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    log "stopping core API (pid $SERVER_PID)..."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# --- prerequisites ----------------------------------------------------------
command -v uv >/dev/null 2>&1 || { err "'uv' not found. Install: https://docs.astral.sh/uv/"; exit 1; }

if [[ ! -d "$VENV" ]]; then
  log "no venv found — running 'make setup' first..."
  make setup
fi

mkdir -p "$LOG_DIR"

# --- start core API in background -------------------------------------------
log "starting core API on ${BASE} (127.0.0.1 only)..."
uv run --python "$VENV" uvicorn core.api:app --host "$HOST" --port "$PORT" \
  >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

# --- wait for /health -------------------------------------------------------
log "waiting for ${BASE}/health ..."
ready=0
for _ in $(seq 1 60); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    err "core API process exited early. Recent log:"
    tail -n 30 "$LOG_FILE" >&2 || true
    exit 1
  fi
  if /usr/bin/curl --fail --silent --max-time 2 "${BASE}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done

if [[ "$ready" -ne 1 ]]; then
  err "core API did not become healthy in time. Recent log:"
  tail -n 30 "$LOG_FILE" >&2 || true
  exit 1
fi
log "core API healthy."

# --- open the UI ------------------------------------------------------------
# Use Python's webbrowser module so this works on Linux AND macOS (no macOS-only
# `open`). Windows users: run `python cli.py serve-open` instead of this script.
log "opening UI: ${BASE}/"
python3 -m webbrowser "${BASE}/" >/dev/null 2>&1 \
  || log "could not open a browser automatically — visit: ${BASE}/"

# --- tail logs (Ctrl-C to stop everything) ----------------------------------
log "tailing ${LOG_FILE} — press Ctrl-C to stop."
tail -n +1 -f "$LOG_FILE"
