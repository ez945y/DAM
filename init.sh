#!/usr/bin/env bash
# init.sh — Agent 啟動腳本
# 一條命令完成：確認環境 → 安裝依賴 → 驗證基線 → 打印啟動命令
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PY=".venv/bin/python"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'

echo -e "${BLUE}[init]${NC} Working directory: $(pwd)"

# Install dependencies (skip if venv exists and looks healthy)
if [ ! -d ".venv" ]; then
    echo -e "${BLUE}[init]${NC} Running: make setup"
    make setup
else
    echo -e "${GREEN}[init]${NC} Reusing existing .venv"
fi

if ! "$PY" -c "import dam, pytest"; then
    echo -e "${RED}[init]${NC} Project Python or dependencies are unavailable. Run: make setup" >&2
    exit 1
fi
echo -e "${BLUE}[init]${NC} Python: $("$PY" --version 2>&1)"

# Verify baseline
echo -e "${BLUE}[init]${NC} Verifying baseline with project Python"
if "$PY" -m pytest tests/unit/ -x --tb=short -q; then
    echo -e "${GREEN}[init] ✓${NC} Baseline verified"
else
    echo -e "${RED}[init] ✗${NC} Baseline verification FAILED"
    echo -e "${RED}[init]${NC} Fix the failing tests before starting new work."
    exit 1
fi

echo ""
echo -e "${GREEN}[init]${NC} Ready. Start with:"
echo -e "  ${BLUE}make dev${NC}"

if [ "${RUN_START_COMMAND:-0}" = "1" ]; then
    echo -e "${BLUE}[init]${NC} Auto-starting..."
    make dev
fi
