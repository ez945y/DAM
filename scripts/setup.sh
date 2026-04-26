#!/usr/bin/env bash
# setup.sh — First-time local dev environment setup
#
# Usage:
#   ./scripts/setup.sh              # simulation mode (no hardware deps)
#   ./scripts/setup.sh --lerobot   # + lerobot + cv2 for SO-ARM101 hardware
#   ./scripts/setup.sh --rust-only  # rebuild Rust extension only
#
# Prerequisites:
#   uv     https://docs.astral.sh/uv/getting-started/installation/
#   cargo  https://rustup.rs/
#   node   https://nodejs.org/  (optional — only needed for the console UI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[setup]${NC} $*"; }
ok()      { echo -e "${GREEN}[setup] ✓${NC} $*"; }
warn()    { echo -e "${YELLOW}[setup] !${NC} $*"; }
die()     { echo -e "${RED}[setup] ✗${NC} $*" >&2; exit 1; }

# ── Argument parsing ───────────────────────────────────────────────────────────
RUST_ONLY=false
WITH_LEROBOT=false
for arg in "$@"; do
    case "$arg" in
        --rust-only)  RUST_ONLY=true ;;
        --lerobot)    WITH_LEROBOT=true ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

cd "$ROOT"

# ── Prerequisite checks / auto-install ────────────────────────────────────────

# Resolve a command that may live in a non-login-shell PATH location.
# Checks common install prefixes before giving up.
_find_cmd() {
    local cmd="$1"
    command -v "$cmd" 2>/dev/null && return 0
    for prefix in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/.rye/shims" "/opt/homebrew/bin" "/usr/local/bin"; do
        [[ -x "$prefix/$cmd" ]] && { echo "$prefix/$cmd"; return 0; }
    done
    return 1
}

need_cmd() {
    _find_cmd "$1" &>/dev/null || die "$1 is required but not found.  $2"
}

# Bring common tool dirs into PATH for non-login shells.
_NVM_BIN="$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)/bin"
for _dir in "$_NVM_BIN" "$HOME/.local/bin" "$HOME/.cargo/bin" "/usr/local/bin" "/opt/homebrew/bin"; do
    [[ -d "$_dir" ]] && export PATH="$_dir:$PATH"
done

# ── Auto-install uv if missing ─────────────────────────────────────────────────
if ! $RUST_ONLY; then
    if ! _find_cmd uv &>/dev/null; then
        info "uv not found — installing via astral.sh installer…"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # The installer puts uv in ~/.local/bin; add it for the rest of this script.
        export PATH="$HOME/.local/bin:$PATH"
        command -v uv &>/dev/null || die "uv install succeeded but still not in PATH — open a new shell and re-run."
        ok "uv installed ($(uv --version))"
    else
        UV="$(_find_cmd uv)"
        # Ensure the resolved path is on PATH for subsequent uv calls
        export PATH="$(dirname "$UV"):$PATH"
    fi
fi

need_cmd cargo "Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"

# ── Python virtual environment ─────────────────────────────────────────────────
if ! $RUST_ONLY; then
    info "Syncing Python environment (uv)…"
    EXTRAS="--extra dev --extra services --extra torch"
    if $WITH_LEROBOT; then
        EXTRAS="$EXTRAS --extra lerobot"
        info "  including lerobot extras (hardware support + cv2)"
    elif [[ -f .venv/bin/python ]] && .venv/bin/python -c "import lerobot" 2>/dev/null; then
        # Preserve lerobot extras if they were previously installed.
        # Running plain `make setup` won't silently remove hardware support.
        WITH_LEROBOT=true
        EXTRAS="$EXTRAS --extra lerobot"
        info "  lerobot detected in existing venv — preserving hardware extras"
    fi
    # Sync environment
    # shellcheck disable=SC2086
    uv sync $EXTRAS

    # Defensive check: Ensure torch isn't a broken namespace package
    if [[ "$EXTRAS" == *"--extra torch"* ]]; then
        info "Verifying PyTorch installation…"
        if ! .venv/bin/python -c "import torch.nn as nn; nn.Module" 2>/dev/null; then
            warn "PyTorch found but appears broken (namespace collision or missing __init__.py)."
            info "  Attempting forced clean install of torch…"
            # We use `uv pip` directly here to force the specific package to refresh
            uv pip install --python .venv/bin/python --force-reinstall torch
        fi
    fi
    ok "Python venv ready (.venv)"
fi

# ── Rust extension — dam_rs ────────────────────────────────────────────────────
info "Building Rust extension (dam_rs) via maturin…"

# cargo lives in ~/.cargo/bin — add it for the maturin build
export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo &>/dev/null || die "cargo not found.  Install rustup: https://rustup.rs/"

WHEEL_DIR="$(mktemp -d)"
# Pass --interpreter explicitly so maturin uses the venv Python (not whatever
# 'python3' resolves to in the current shell — which may be conda's Python).
(
    cd "$ROOT/dam-rust/dam-py"
    "$ROOT/.venv/bin/maturin" build --release \
        --interpreter "$ROOT/.venv/bin/python" \
        --out "$WHEEL_DIR"
)
# uv is a standalone binary, not inside the venv.  Use the resolved path.
UV="$(_find_cmd uv)"
"$UV" pip install --python "$ROOT/.venv/bin/python" \
    --find-links "$WHEEL_DIR" "dam-rs" --force-reinstall --quiet
rm -rf "$WHEEL_DIR"

"$ROOT/.venv/bin/python" -c "import dam_rs" \
    || die "dam_rs wheel installed but import failed — check Rust build output above."
ok "dam_rs installed into .venv"

# ── Frontend (optional) ────────────────────────────────────────────────────────
if ! $RUST_ONLY; then
    if command -v node &>/dev/null; then
        info "Installing frontend dependencies (npm)…"
        (cd "$ROOT/dam-console" && npm install --silent)

        # Provision .env.local from example if it doesn't exist
        if [[ ! -f "$ROOT/dam-console/.env.local" ]]; then
            if [[ -f "$ROOT/dam-console/.env.local.example" ]]; then
                cp "$ROOT/dam-console/.env.local.example" "$ROOT/dam-console/.env.local"
            else
                printf 'NEXT_PUBLIC_API_URL=http://localhost:8080\nNEXT_PUBLIC_WS_URL=ws://localhost:8080\n' \
                    > "$ROOT/dam-console/.env.local"
            fi
            info "Created dam-console/.env.local"
        fi
        ok "Frontend ready"
    else
        warn "node not found — skipping frontend setup (backend-only mode)"
    fi
fi

# ── Pre-commit Hooks ──────────────────────────────────────────────────────────
if ! $RUST_ONLY && [[ -f .pre-commit-config.yaml ]]; then
    info "Initializing pre-commit hooks..."
    # pre-commit is already installed via 'uv sync' (dev extra) above
    "$ROOT/.venv/bin/pre-commit" install
    ok "Pre-commit hooks initialized"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
ok "Setup complete."
echo -e "  Run ${GREEN}make dev${NC}          to start hot-reload dev server (backend + Next.js dev)."
echo -e "  Run ${GREEN}make run${NC}          to build & start production server."
if $WITH_LEROBOT; then
    echo -e "  Hardware support enabled — connect robot and run ${GREEN}make run${NC} or ${GREEN}make dev${NC}."
else
    echo -e "  For real hardware: ${GREEN}make setup-lerobot${NC} then ${GREEN}make dev${NC}."
fi
echo -e "  Run ${GREEN}make docs${NC}         to preview documentation → http://localhost:8002"
echo -e "  Run ${GREEN}make test${NC}         to run the test suite."
