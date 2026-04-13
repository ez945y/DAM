# DAM — local development helpers
#
# Quick start:
#   make setup   ← run once after cloning
#   make run     ← start backend + frontend
#   make test    ← run the full test suite
#
# CI jobs (GitHub Actions):
#   make ci-lint           ← ruff format + lint
#   make ci-syntax        ← AST parse check
#   make ci-import         ← build Rust + import
#   make ci-stackfile     ← validate stackfile
#
# Docker alternative: see docker-compose.yml
.PHONY: setup run test test-py test-rs test-ui lint build-rs clean help ci-lint ci-syntax ci-import ci-stackfile

# Ensure scripts are executable before every target that uses them
_chmod:
	@chmod +x scripts/setup.sh scripts/run.sh scripts/test.sh

setup: _chmod   ## First-time setup: Python venv (uv) + Rust extension (maturin) + npm
	@bash scripts/setup.sh

setup-lerobot: _chmod   ## Setup with lerobot hardware support (SO-ARM101 + cameras)
	@bash scripts/setup.sh --lerobot

build-rs: _chmod   ## Rebuild Rust extension only (dam_rs via maturin)
	@bash scripts/setup.sh --rust-only

run: _chmod   ## Start backend + frontend (follows .dam_stackfile.yaml config)
	@BACKEND_SCRIPT=scripts/dam_host.py bash scripts/run.sh

test: _chmod   ## Run all tests + linters (Python, Rust, frontend)
	@bash scripts/test.sh

test-py: _chmod   ## Python tests only (unit + integration + safety + property)
	@bash scripts/test.sh --python

test-rs: _chmod   ## Rust tests only (cargo test --workspace)
	@bash scripts/test.sh --rust

test-ui: _chmod   ## Frontend tests only (jest --ci)
	@bash scripts/test.sh --frontend

lint: _chmod   ## Linters only (ruff, mypy, cargo clippy)
	@bash scripts/test.sh --lint

clean:   ## Remove venv, Rust build artefacts, and node_modules
	rm -rf .venv dam-rust/target dam-console/node_modules dam-console/.next

help:   ## Show this help message
	@echo ""
	@echo "  DAM — local development targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""

# CI targets (mirrors GitHub Actions)
ci-lint: _chmod
	@bash scripts/test.sh --lint

ci-syntax:
	@python -c "import ast; import glob; [ast.parse(open(f).read()) for f in glob.glob('dam/**/*.py', recursive=True)]"

ci-import: _chmod
	@bash scripts/setup.sh --rust-only
	@python -c "import dam; print('OK')"

ci-stackfile: _chmod
	@python -c "from dam.config.loader import StackfileLoader; StackfileLoader.validate('examples/stackfiles/test.yaml')"