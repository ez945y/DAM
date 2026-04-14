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
info() { echo -e "${BLUE}[dev]${NC} $*"; }
die()  { echo -e "${RED}[dev] ✗${NC} $*" >&2; exit 1; }

cd "$ROOT"

# Ensure the project root is on PYTHONPATH so `import dam` works when Python
# is invoked as a script (sys.path[0] = script dir, not project root).
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Bring cargo, uv, and node/npm into PATH if not already present.
# This handles environments where these tools are installed but not on the
# default non-login shell PATH (conda, nvm, homebrew, etc.).
# Prepend in low→high priority order (last entry wins).
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
    echo -e "${RED}[dev] dam_rs import error:${NC}"
    .venv/bin/python -c "import dam_rs" 2>&1 || true
    die "dam_rs not importable.  Run: make build-rs"
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

# ── Graceful shutdown ─────────────────────────────────────────────────────────
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

# Dynamic backend script selection (default to simulation)
BACKEND_BIN="${BACKEND_SCRIPT:-scripts/dam_sim.py}"

info "Starting backend (${BACKEND_BIN}) on :8080 …"
.venv/bin/python "$BACKEND_BIN" &
_child_pids+=($!)
BACKEND_PID=$!

# Poll until the health endpoint responds (up to 60 s)
info "Waiting for backend to be ready…"
READY=false
for i in $(seq 1 60); do
    if .venv/bin/python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/control/status')" \
        2>/dev/null; then
        READY=true
        break
    fi
    # Bail early if the backend process died
    kill -0 "$BACKEND_PID" 2>/dev/null || die "Backend exited unexpectedly."
    sleep 1
done
$READY || die "Backend did not become ready after 60 s."

# ── Frontend (optional) ───────────────────────────────────────────────────────
if [[ -d dam-console/node_modules ]]; then
    info "Starting frontend (Next.js) on :3000 …"
    (cd dam-console && HOST=127.0.0.1 PORT=3000 npm run dev) &
    _child_pids+=($!)
else
    echo -e "${YELLOW}[dev] !${NC} dam-console/node_modules not found — skipping frontend."
    echo -e "      Run ${GREEN}make setup${NC} to install it."
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

wait
