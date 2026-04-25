# DAM — local development helpers
#
# Quick start:
#   make setup   ← run once after cloning
#   make dev     ← hot-reload dev server (backend + Next.js dev)
#   make run     ← production mode (build frontend + start backend)
#   make test    ← run the full test suite
#   make docs    ← preview documentation (mkdocs serve)
#
# CI jobs (GitHub Actions):
#   make ci-lint           ← ruff format + lint
#   make ci-syntax        ← AST parse check
#   make ci-import         ← build Rust + import
#   make ci-stackfile     ← validate stackfile
#
.PHONY: setup dev run docs test test-py test-rs test-ui lint build-rs clean help ci-lint ci-syntax ci-import ci-stackfile _kill_port

# Ensure scripts are executable before every target that uses them
_chmod:
	@chmod +x scripts/setup.sh scripts/run.sh scripts/run_prod.sh scripts/test.sh

_kill_port:
	@echo "Checking for processes on port 8080..."
	@lsof -ti:8080 | xargs kill -9 2>/dev/null || true

setup: _chmod   ## First-time setup: venv + Rust + npm + pre-commit hooks
	@bash scripts/setup.sh

setup-lerobot: _chmod   ## Setup with lerobot hardware support (SO-ARM101 + cameras)
	@bash scripts/setup.sh --lerobot

build-rs: _chmod   ## Rebuild Rust extension only (dam_rs via maturin)
	@bash scripts/setup.sh --rust-only

setup-precommit:   ## Install and setup pre-commit hooks
	@uv pip install pre-commit --python .venv/bin/python
	@.venv/bin/pre-commit install

# macOS: prefer venv's bundled ffmpeg dylibs over Homebrew to silence
# "Class AVFAudioReceiver is implemented in both …" ObjC duplicate warnings.
_AV_DYLIB_DIR := $(shell ls -d .venv/lib/python*/site-packages/av/.dylibs 2>/dev/null | head -1)
_DYLD_PREFIX   := $(if $(_AV_DYLIB_DIR),DYLD_LIBRARY_PATH="$(_AV_DYLIB_DIR):$$DYLD_LIBRARY_PATH" ,)
_PYTHONPATH    := PYTHONPATH="$(shell python3 -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else '')" 2>/dev/null):$$PYTHONPATH"

dev: _chmod _kill_port  ## Dev mode: hot-reload backend + Next.js dev server
	@$(_DYLD_PREFIX)$(_PYTHONPATH) BACKEND_SCRIPT=scripts/dam_host.py bash scripts/run.sh

run: _chmod _kill_port  ## Production mode: build frontend (static) + start backend
	@$(_DYLD_PREFIX)$(_PYTHONPATH) bash scripts/run_prod.sh

docs:   ## Preview documentation locally at http://127.0.0.1:8002/DAM/
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:/opt/homebrew/bin:$$PATH"; \
	 uv pip install --python .venv/bin/python mkdocs mkdocs-material pymdown-extensions --quiet
	@.venv/bin/mkdocs serve --dev-addr 127.0.0.1:8002

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
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
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
