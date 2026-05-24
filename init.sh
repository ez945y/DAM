#!/usr/bin/env bash
# init.sh — Agent 啟動腳本
# 一條命令完成：確認環境 → 安裝依賴 → 驗證基線 → 打印啟動命令
set -euo pipefail

INSTALL_CMD="make setup"
VERIFY_CMD="python -m pytest tests/unit/ -x --tb=short -q"
START_CMD="make dev"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'

echo -e "${BLUE}[init]${NC} Working directory: $(pwd)"
echo -e "${BLUE}[init]${NC} Python: $(python --version 2>&1)"

# Install dependencies (skip if venv exists and looks healthy)
if [ ! -d ".venv" ]; then
    echo -e "${BLUE}[init]${NC} Running: $INSTALL_CMD"
    eval "$INSTALL_CMD"
else
    echo -e "${GREEN}[init]${NC} .venv exists, skipping install"
fi

# Verify baseline
echo -e "${BLUE}[init]${NC} Verifying baseline: $VERIFY_CMD"
if eval "$VERIFY_CMD"; then
    echo -e "${GREEN}[init] ✓${NC} Baseline verified"
else
    echo -e "${RED}[init] ✗${NC} Baseline verification FAILED"
    echo -e "${RED}[init]${NC} Fix the failing tests before starting new work."
    exit 1
fi

echo ""
echo -e "${GREEN}[init]${NC} Ready. Start with:"
echo -e "  ${BLUE}$START_CMD${NC}"

if [ "${RUN_START_COMMAND:-0}" = "1" ]; then
    echo -e "${BLUE}[init]${NC} Auto-starting..."
    eval "$START_CMD"
fi
