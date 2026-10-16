#!/usr/bin/env bash
# run_prod.sh — Production mode: start backend + an existing Next.js build
#
# Differences from dev mode (run.sh):
#   • Frontend serves a pre-built production bundle (optimised, no hot-reload)
#   • Frontend is served from Next.js standalone output
#   • Backend uses scripts/dam_host.py (real hardware / stackfile config)
#
# Usage:
#   make build        ← after frontend changes
#   make run          ← start an existing production build
#   bash scripts/run_prod.sh   ← direct
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${BLUE}[run]${NC} $*"; return 0; }
die()  { echo -e "${RED}[run] ✗${NC} $*" >&2; exit 1; }

cd "$ROOT"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Bring cargo, uv, and node/npm into PATH if not already present.
_NVM_BIN="$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1 || true)/bin"
for _dir in "$_NVM_BIN" "$HOME/.local/bin" "$HOME/.cargo/bin" "/usr/local/bin" "/opt/homebrew/bin"; do
    [[ -d "$_dir" ]] && export PATH="$_dir:$PATH"
done

# ── Preflight checks ───────────────────────────────────────────────────────────
[[ -d .venv ]] \
    || die ".venv not found.  Run: make setup"
[[ -f .venv/bin/python ]] \
    || die ".venv is missing a Python binary.  Run: make setup"
command -v curl >/dev/null 2>&1 || die "curl is required for service readiness checks."
.venv/bin/python scripts/check_ports.py 8080 3000
[[ -f dam-console/.next/standalone/server.js ]] \
    || die "Frontend not built. Run: make build"
if ! .venv/bin/python -c "import dam_rs; w=dam_rs.McapWriter(); assert hasattr(dam_rs, 'ImageHub'); assert hasattr(w, 'attach_image_hub'); assert hasattr(w, 'stop')" 2>/dev/null; then
    .venv/bin/python -c "import dam_rs; w=dam_rs.McapWriter(); print(dam_rs.__file__); print('ImageHub', hasattr(dam_rs, 'ImageHub')); print('attach_image_hub', hasattr(w, 'attach_image_hub')); print('stop', hasattr(w, 'stop'))" 2>&1 || true
    die "dam_rs is stale or incomplete. Run: make build-rs"
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
mkdir -p "$ROOT/logs"
SERVICE_LOG_DIR="$(mktemp -d "$ROOT/logs/run.XXXXXX")"
BACKEND_RUN_LOG="$SERVICE_LOG_DIR/backend.log"
FRONTEND_RUN_LOG="$SERVICE_LOG_DIR/frontend.log"
source "$SCRIPT_DIR/service_helpers.sh"
info "Service logs: ${SERVICE_LOG_DIR}"

# ── Start backend (Fast background startup) ──────────────────────────────────
info "Starting backend (scripts/dam_host.py) on :8080…"
.venv/bin/python -u scripts/dam_host.py > "$BACKEND_RUN_LOG" 2>&1 &
BACKEND_PID=$!
register_service "$BACKEND_PID" "Backend API" "$BACKEND_RUN_LOG"
wait_for_http "Backend API" "http://127.0.0.1:8080/api/control/status" "$BACKEND_RUN_LOG"

# ── Start production frontend ──────────────────────────────────────────────────
info "Starting frontend (Next.js standalone) on :3000…"
info "  run log → ${FRONTEND_RUN_LOG}"
# standalone mode requires `node .next/standalone/server.js`
(cd dam-console && exec env PORT=3000 HOSTNAME=127.0.0.1 node .next/standalone/server.js) > "$FRONTEND_RUN_LOG" 2>&1 &
FRONTEND_PID=$!
register_service "$FRONTEND_PID" "Frontend console" "$FRONTEND_RUN_LOG"
wait_for_http "Frontend console" "http://127.0.0.1:3000/" "$FRONTEND_RUN_LOG"

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

monitor_services
