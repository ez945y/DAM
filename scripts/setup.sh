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
info()    { echo -e "${BLUE}[setup]${NC} $*"; return 0; }
ok()      { echo -e "${GREEN}[setup] ✓${NC} $*"; return 0; }
warn()    { echo -e "${YELLOW}[setup] !${NC} $*"; return 0; }
die()     { echo -e "${RED}[setup] ✗${NC} $*" >&2; exit 1; }

# ── Argument parsing ───────────────────────────────────────────────────────────
RUST_ONLY=false
WITH_LEROBOT=false
WITH_ROS=false
for arg in "$@"; do
    case "$arg" in
        --rust-only)  RUST_ONLY=true ;;
        --lerobot)    WITH_LEROBOT=true ;;
        --ros)        WITH_ROS=true ;;
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
        if [[ -x "$prefix/$cmd" ]]; then
            echo "$prefix/$cmd"
            return 0
        fi
    done
    return 1
}

need_cmd() {
    local cmd="$1" hint="$2"
    _find_cmd "$cmd" &>/dev/null || die "$cmd is required but not found.  $hint"
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
    # torch, lerobot, and proxsuite are core dependencies of robot-dam now, so
    # they are always installed by 'uv sync'. proxsuite backs the QP/CBF solver
    # used by L1 boundaries.
    EXTRAS="--extra dev --extra services"
    if $WITH_LEROBOT; then
        info "  lerobot hardware support is included by default"
    elif [[ -f .venv/bin/python ]] && .venv/bin/python -c "import lerobot" 2>/dev/null; then
        WITH_LEROBOT=true
    fi
    if $WITH_ROS; then
        EXTRAS="$EXTRAS --extra ros2"
        info "  including ros2 extras (transforms3d; install the ROS2 distro separately via apt)"
    elif [[ -f .venv/bin/python ]] && .venv/bin/python -c "import transforms3d" 2>/dev/null; then
        # Preserve ros2 extras if they were previously installed.
        WITH_ROS=true
        EXTRAS="$EXTRAS --extra ros2"
        info "  ros2 deps detected in existing venv — preserving ROS2 support"
    fi
    # Sync environment
    # shellcheck disable=SC2086
    uv sync --frozen --inexact $EXTRAS

    # Defensive check: Ensure torch isn't a broken namespace package
    info "Verifying PyTorch installation…"
    if ! .venv/bin/python -c "import torch.nn as nn; nn.Module" 2>/dev/null; then
        warn "PyTorch found but appears broken (namespace collision or missing __init__.py)."
        info "  Attempting forced clean install of torch…"
        # We use `uv pip` directly here to force the specific package to refresh
        uv pip install --python .venv/bin/python --force-reinstall torch
    fi
    ok "Python venv ready (.venv)"
fi

# ── Version sync — single source of truth is the root pyproject.toml ──────────
# Propagates [project].version to dam-rust/dam-py, every crate Cargo.toml,
# Cargo.lock, uv.lock, the console package.json and mkdocs.yml. Without this
# the maturin build below would bake a stale dam-rs version.
info "Syncing versions from pyproject.toml…"
_SYNC_PY="$ROOT/.venv/bin/python"
[[ -x "$_SYNC_PY" ]] || _SYNC_PY="$(command -v python3 || true)"
if [[ -n "$_SYNC_PY" ]]; then
    "$_SYNC_PY" "$ROOT/scripts/sync_version.py" \
        || warn "sync_version.py failed — package versions may be inconsistent"
else
    warn "No Python found to run sync_version.py — package versions may be inconsistent"
fi

# ── Rust extension — dam_rs ────────────────────────────────────────────────────
info "Building Rust extension (dam_rs) via maturin…"

# cargo lives in ~/.cargo/bin — add it for the maturin build
export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo &>/dev/null || die "cargo not found.  Install rustup: https://rustup.rs/"

_rust_stamp() {
    (
        cd "$ROOT"
        "$ROOT/.venv/bin/python" - <<'PY'
import sysconfig
print(sysconfig.get_config_var("SOABI") or "")
PY
        find dam-rust -type f \( \
            -name '*.rs' -o \
            -name 'Cargo.toml' -o \
            -name 'Cargo.lock' -o \
            -name 'pyproject.toml' \
        \) -not -path '*/target/*' | LC_ALL=C sort | xargs shasum
    ) | shasum | awk '{print $1}'
}

RUST_STAMP_FILE="$ROOT/.venv/.dam_rs_build.stamp"
RUST_STAMP="$(_rust_stamp)"
if ! $RUST_ONLY \
    && [[ -f "$RUST_STAMP_FILE" ]] \
    && [[ "$(cat "$RUST_STAMP_FILE")" == "$RUST_STAMP" ]] \
    && "$ROOT/.venv/bin/python" -c "import dam_rs" 2>/dev/null; then
    ok "dam_rs already up to date; skipping Rust rebuild"
else
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
    echo "$RUST_STAMP" > "$RUST_STAMP_FILE"
fi

"$ROOT/.venv/bin/python" -c "import dam_rs" \
    || die "dam_rs wheel installed but import failed — check Rust build output above."
ok "dam_rs installed into .venv"

# Install the dam project itself (editable) so the `dam` console command is
# available.  --no-deps: dependencies are already provisioned above; avoid
# re-resolving and disturbing the locally-built dam_rs / torch wheels.
"$UV" pip install --python "$ROOT/.venv/bin/python" -e "$ROOT" --no-deps --quiet
"$ROOT/.venv/bin/python" -c "import dam" \
    || die "dam package install failed."
ok "dam CLI installed (.venv/bin/dam)"

# ── Frontend (optional) ────────────────────────────────────────────────────────
if ! $RUST_ONLY; then
    if command -v node &>/dev/null; then
        info "Installing frontend dependencies (npm)…"
        _frontend_stamp() {
            (
                cd "$ROOT/dam-console"
                node --version
                shasum package.json package-lock.json 2>/dev/null || shasum package.json
            ) | shasum | awk '{print $1}'
        }

        FRONTEND_STAMP_FILE="$ROOT/dam-console/node_modules/.dam_npm_install.stamp"
        FRONTEND_STAMP="$(_frontend_stamp)"
        if [[ -f "$FRONTEND_STAMP_FILE" ]] \
            && [[ "$(cat "$FRONTEND_STAMP_FILE")" == "$FRONTEND_STAMP" ]] \
            && [[ -x "$ROOT/dam-console/node_modules/.bin/next" ]]; then
            ok "Frontend dependencies already up to date; skipping npm install"
        elif [[ -f "$ROOT/dam-console/package-lock.json" ]]; then
            (cd "$ROOT/dam-console" && npm ci --prefer-offline --no-audit --silent)
            echo "$FRONTEND_STAMP" > "$FRONTEND_STAMP_FILE"
        else
            (cd "$ROOT/dam-console" && npm install --prefer-offline --no-audit --silent)
            echo "$FRONTEND_STAMP" > "$FRONTEND_STAMP_FILE"
        fi

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

# ── Rerun Viewer (for lerobot display_data) ───────────────────────────────────
if ! $RUST_ONLY; then
    if ! "$ROOT/.venv/bin/python" -c "import rerun" 2>/dev/null; then
        info "Installing Rerun SDK (visualization for recording)…"
        "$UV" pip install --python "$ROOT/.venv/bin/python" rerun-sdk --quiet
        ok "Rerun SDK installed"
    else
        ok "Rerun SDK already installed"
    fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
ok "Setup complete."
echo -e "  Run ${GREEN}make dev${NC}          to start hot-reload dev server (backend + Next.js dev)."
echo -e "  Run ${GREEN}make build${NC}        after UI changes; ${GREEN}make run${NC} starts production server."
if $WITH_LEROBOT; then
    echo -e "  Hardware support enabled — connect robot and run ${GREEN}make run${NC} or ${GREEN}make dev${NC}."
else
    echo -e "  For real hardware: ${GREEN}make setup-lerobot${NC} then ${GREEN}make dev${NC}."
fi
echo -e "  Run ${GREEN}make docs${NC}         to preview documentation → http://localhost:8002"
echo -e "  Run ${GREEN}make test${NC}         to run the test suite."
