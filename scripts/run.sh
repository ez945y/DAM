#!/usr/bin/env bash
# dev.sh — Start DAM dev services locally (no Docker required)
#
# Starts:
#   • FastAPI backend  → http://localhost:8080  (REST API + WebSocket)
#   • Swagger docs     → http://localhost:8080/docs
#   • Next.js frontend → http://localhost:3000  (dashboard)
#
# Prerequisites: run ./scripts/setup.sh (or make setup) first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info() { echo -e "${BLUE}[dev]${NC} $*"; return 0; }
die()  { echo -e "${RED}[dev] ✗${NC} $*" >&2; exit 1; }

cd "$ROOT"

# Ensure the project root is on PYTHONPATH so `import dam` works when Python
# is invoked as a script (sys.path[0] = script dir, not project root).
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Bring cargo, uv, and node/npm into PATH if not already present.
# This handles environments where these tools are installed but not on the
# default non-login shell PATH (conda, nvm, homebrew, etc.).
# Prepend in low→high priority order (last entry wins).
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
if [[ -d dam-console/node_modules ]]; then
    command -v npm >/dev/null 2>&1 || die "npm not found. Install Node.js before starting the console."
fi
_ports=(8080)
[[ ! -d dam-console/node_modules ]] || _ports+=(3000)
.venv/bin/python scripts/check_ports.py "${_ports[@]}"
if ! .venv/bin/python -c "import dam_rs; w=dam_rs.McapWriter(); assert hasattr(dam_rs, 'ImageHub'); assert hasattr(w, 'attach_image_hub'); assert hasattr(w, 'stop')" 2>/dev/null; then
    echo -e "${RED}[dev] dam_rs import error:${NC}" >&2
    .venv/bin/python -c "import dam_rs; w=dam_rs.McapWriter(); print(dam_rs.__file__); print('ImageHub', hasattr(dam_rs, 'ImageHub')); print('attach_image_hub', hasattr(w, 'attach_image_hub')); print('stop', hasattr(w, 'stop'))" 2>&1 || true
    die "dam_rs is stale or incomplete. Run: make build-rs"
fi

# ── Stackfile Initialization ──────────────────────────────────────────────────
STACKFILE=".dam_stackfile.yaml"
DEMO_STACKFILE="examples/stackfiles/demo.yaml"

if [[ ! -f "$STACKFILE" ]]; then
    info "${YELLOW}${STACKFILE}${NC} not found."
    if [[ -f "$DEMO_STACKFILE" ]]; then
        info "Copying ${GREEN}${DEMO_STACKFILE}${NC} to ${GREEN}${STACKFILE}${NC}…"
        cp "$DEMO_STACKFILE" "$STACKFILE"
    else
        die "Default stackfile ${DEMO_STACKFILE} not found. Cannot initialize."
    fi
fi

# Keep separate logs for each invocation; failures print their recent output.
mkdir -p "$ROOT/logs"
SERVICE_LOG_DIR="$(mktemp -d "$ROOT/logs/dev.XXXXXX")"
BACKEND_RUN_LOG="$SERVICE_LOG_DIR/backend.log"
FRONTEND_RUN_LOG="$SERVICE_LOG_DIR/frontend.log"
source "$SCRIPT_DIR/service_helpers.sh"
info "Service logs: ${SERVICE_LOG_DIR}"

# Dynamic backend script selection (default to Universal Host)
BACKEND_BIN="${BACKEND_SCRIPT:-scripts/dam_host.py}"

info "Starting backend (${BACKEND_BIN}) on :8080 …"
.venv/bin/python -u "$BACKEND_BIN" > "$BACKEND_RUN_LOG" 2>&1 &
BACKEND_PID=$!
register_service "$BACKEND_PID" "Backend API" "$BACKEND_RUN_LOG"

# ── Frontend (parallel startup) ───────────────────────────────────────────────
if [[ -d dam-console/node_modules ]]; then
    info "Starting frontend (Next.js) on :3000 …"
    (cd dam-console && exec npm run dev -- --hostname 127.0.0.1 --port 3000) > "$FRONTEND_RUN_LOG" 2>&1 &
    register_service "$!" "Frontend console" "$FRONTEND_RUN_LOG"
else
    echo -e "${YELLOW}[dev] !${NC} dam-console/node_modules not found — skipping frontend."
    echo -e "      Run ${GREEN}make setup${NC} to install it."
fi

wait_for_http "Backend API" "http://127.0.0.1:8080/api/control/status" "$BACKEND_RUN_LOG"
if [[ -d dam-console/node_modules ]]; then
    wait_for_http "Frontend console" "http://127.0.0.1:3000/" "$FRONTEND_RUN_LOG"
fi

# ── Ready banner ──────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  DAM dev server ready${NC}"
echo -e "  API:     ${CYAN}http://localhost:8080${NC}"
echo -e "  Swagger: ${CYAN}http://localhost:8080/docs${NC}"
[[ -d dam-console/node_modules ]] && \
echo -e "  Console: ${CYAN}http://localhost:3000${NC}"
echo -e "  Docs:    run ${GREEN}make docs${NC} in another terminal → http://127.0.0.1:8002/DAM/"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all services."
echo ""

monitor_services
