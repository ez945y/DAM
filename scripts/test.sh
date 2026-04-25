#!/usr/bin/env bash
# test.sh — Run the DAM test suite locally
#
# Usage:
#   ./scripts/test.sh               # all tests + linters
#   ./scripts/test.sh --python      # Python tests only
#   ./scripts/test.sh --rust        # Rust tests only
#   ./scripts/test.sh --frontend    # Frontend (Jest) tests only
#   ./scripts/test.sh --lint        # Linters only (ruff, mypy, clippy)
#   ./scripts/test.sh --no-lint     # All tests, skip linters
#
# Exit code: 0 = all checks passed, 1 = one or more checks failed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}[test]   ${NC} $*"; }
ok()      { echo -e "${GREEN}[test] ✓ ${NC} $*"; }
fail()    { echo -e "${RED}[test] ✗ ${NC} $*"; }
section() {
    local title="── $* "
    local len=${#title}
    local total=72
    local dashes=$((total - len))
    local line=""
    if [ $dashes -gt 0 ]; then
        line=$(printf '─%.0s' $(seq 1 $dashes))
    fi
    echo -e "\n${BOLD}${BLUE}${title}${line}${NC}"
}

cd "$ROOT"

# Ensure dam package is importable when pytest runs test files as scripts.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Fix PyO3 compatibility for systems with Python 3.14 (Mac default/brew)
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

# Bring cargo, uv, and node/npm into PATH if not already present.
_NVM_BIN="$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)/bin"
for _dir in "$_NVM_BIN" "$HOME/.local/bin" "$HOME/.cargo/bin" "/usr/local/bin" "/opt/homebrew/bin"; do
    [[ -d "$_dir" ]] && export PATH="$_dir:$PATH"
done

# ── Argument parsing ───────────────────────────────────────────────────────────
RUN_PYTHON=true; RUN_RUST=true; RUN_FRONTEND=true; RUN_LINT=true

for arg in "$@"; do
    case "$arg" in
        --python)   RUN_PYTHON=true;  RUN_RUST=false; RUN_FRONTEND=false; RUN_LINT=false ;;
        --rust)     RUN_PYTHON=false; RUN_RUST=true;  RUN_FRONTEND=false; RUN_LINT=false ;;
        --frontend) RUN_PYTHON=false; RUN_RUST=false; RUN_FRONTEND=true;  RUN_LINT=false ;;
        --lint)     RUN_PYTHON=false; RUN_RUST=false; RUN_FRONTEND=false; RUN_LINT=true  ;;
        --no-lint)  RUN_LINT=false ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# ── Preflight ──────────────────────────────────────────────────────────────────
[[ -d .venv ]] || { echo -e "${RED}ERROR:${NC} .venv not found.  Run: make setup" >&2; exit 1; }

PY=".venv/bin/python"
FAILURES=()

# run_step LABEL CMD [ARGS…]  — runs the command, records pass/fail
run_step() {
    local label="$1"; shift
    info "$label"
    if "$@"; then
        ok "$label"
    else
        fail "$label"
        FAILURES+=("$label")
    fi
}

# ── Linters ────────────────────────────────────────────────────────────────────
if $RUN_LINT; then
    section "Linters"
    run_step "pre-commit" .venv/bin/pre-commit run --all-files
fi

# ── Python tests ───────────────────────────────────────────────────────────────
if $RUN_PYTHON; then
    section "Python — unit"
    run_step "pytest unit"         "$PY" -m pytest tests/unit/        -v --tb=short

    section "Python — integration"
    run_step "pytest integration"  "$PY" -m pytest tests/integration/ -v --tb=short -m "not hardware"

    section "Python — safety"
    run_step "pytest safety"       "$PY" -m pytest tests/safety/      -v --tb=short

    section "Python — property"
    run_step "pytest property"     "$PY" -m pytest tests/property/    -v --tb=short -m "not slow"
fi

# ── Rust tests ─────────────────────────────────────────────────────────────────
if $RUN_RUST; then
    if command -v cargo &>/dev/null; then
        section "Rust"
        run_step "cargo test" bash -c "cd dam-rust && cargo test --workspace"
    else
        echo -e "${YELLOW}[test] !${NC} cargo not found — skipping Rust tests"
    fi
fi

# ── Frontend tests ─────────────────────────────────────────────────────────────
if $RUN_FRONTEND; then
    if [[ -d dam-console/node_modules ]]; then
        section "Frontend (Jest)"
        run_step "jest" bash -c "cd dam-console && npm run test:ci"
    else
        echo -e "${YELLOW}[test] !${NC} dam-console/node_modules not found — skipping frontend tests"
    fi
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed.${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}${#FAILURES[@]} check(s) failed:${NC}"
    for f in "${FAILURES[@]}"; do
        echo -e "  ${RED}✗${NC} $f"
    done
    exit 1
fi
