#!/usr/bin/env bash
# run_prod.sh — Production mode: build Next.js frontend + start backend
#
# Differences from dev mode (run.sh):
#   • Frontend is compiled once with `npm run build` (optimised, no hot-reload)
#   • Frontend is served with `npm run start` (Next.js production server)
#   • Backend uses scripts/dam_host.py (real hardware / stackfile config)
#
# Usage:
#   make run          ← recommended
#   bash scripts/run_prod.sh   ← direct
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${BLUE}[run]${NC} $*"; }
ok()   { echo -e "${GREEN}[run] ✓${NC} $*"; }
die()  { echo -e "${RED}[run] ✗${NC} $*" >&2; exit 1; }

cd "$ROOT"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Bring cargo, uv, and node/npm into PATH if not already present.
_NVM_BIN="$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)/bin"
for _dir in "$_NVM_BIN" "$HOME/.local/bin" "$HOME/.cargo/bin" "/usr/local/bin" "/opt/homebrew/bin"; do
    [[ -d "$_dir" ]] && export PATH="$_dir:$PATH"
done

# ── Preflight checks ───────────────────────────────────────────────────────────
[[ -d .venv ]] \
    || die ".venv not found.  Run: make setup"
[[ -f .venv/bin/python ]] \
    || die ".venv is missing a Python binary.  Run: make setup"
if ! .venv/bin/python -c "import dam_rs" 2>/dev/null; then
    die "dam_rs not importable.  Run: make build-rs"
fi
command -v node &>/dev/null \
    || die "node not found.  Install from https://nodejs.org/"
[[ -d dam-console/node_modules ]] \
    || die "dam-console/node_modules not found.  Run: make setup"

# ── Stackfile initialization ───────────────────────────────────────────────────
STACKFILE=".dam_stackfile.yaml"
DEMO_STACKFILE="examples/stackfiles/demo.yaml"
if [[ ! -f "$STACKFILE" ]]; then
    info "${STACKFILE} not found."
    if [[ -f "$DEMO_STACKFILE" ]]; then
        info "Copying ${DEMO_STACKFILE} → ${STACKFILE}…"
        cp "$DEMO_STACKFILE" "$STACKFILE"
    else
        die "Default stackfile ${DEMO_STACKFILE} not found."
    fi
fi

# ── Build frontend ─────────────────────────────────────────────────────────────
info "Building frontend (Next.js production build)…"
(cd dam-console && npm run build)

# standalone output needs static assets copied in manually
info "Copying static assets into standalone bundle…"
cp -r dam-console/public dam-console/.next/standalone/public 2>/dev/null || true
cp -r dam-console/.next/static dam-console/.next/standalone/.next/static 2>/dev/null || true
ok "Frontend built"

# ── Graceful shutdown ──────────────────────────────────────────────────────────
_child_pids=()
_shutdown() {
    echo ""
    info "Shutting down…"
    for pid in "${_child_pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    info "All services stopped."
}
trap _shutdown INT TERM EXIT

# ── Start backend ──────────────────────────────────────────────────────────────
info "Starting backend (scripts/dam_host.py) on :8080…"
.venv/bin/python scripts/dam_host.py &
_child_pids+=($!)
BACKEND_PID=$!

info "Waiting for backend to be ready…"
READY=false
for i in $(seq 1 60); do
    if .venv/bin/python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/control/status')" \
        2>/dev/null; then
        READY=true
        break
    fi
    kill -0 "$BACKEND_PID" 2>/dev/null || die "Backend exited unexpectedly."
    sleep 1
done
$READY || die "Backend did not become ready after 60 s."

# ── Start production frontend ──────────────────────────────────────────────────
info "Starting frontend (Next.js standalone) on :3000…"
# standalone mode requires `node .next/standalone/server.js` — `npm run start` is incompatible
(cd dam-console && PORT=3000 HOSTNAME=127.0.0.1 node .next/standalone/server.js) &
_child_pids+=($!)

# ── Ready banner ───────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  DAM production server ready${NC}"
echo -e "  API:     ${CYAN}http://localhost:8080${NC}"
echo -e "  Swagger: ${CYAN}http://localhost:8080/docs${NC}"
echo -e "  Console: ${CYAN}http://localhost:3000${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all services."
echo ""

wait
