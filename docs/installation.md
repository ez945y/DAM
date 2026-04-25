# Installation

DAM is set up from source using `make setup`. The setup script handles the Python environment, Rust extension build, and frontend dependencies in one step.

---

## Prerequisites

| Tool | How to get it |
|------|--------------|
| **Git** | Already installed on most systems |
| **uv** | Auto-installed by `make setup` if missing |
| **Rust + cargo** | [rustup.rs](https://rustup.rs/) — required for the Rust data plane |
| **Node.js** | [nodejs.org](https://nodejs.org/) — optional, only needed for the dashboard UI |

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/ez945y/DAM.git
cd DAM

# 2. Run one-time setup (Python venv + Rust extension + npm)
make setup

# 3. Start the system
make run
```

`make run` starts both the backend API (port 8080) and the dashboard UI (port 3000).
Open **http://localhost:3000** in your browser to see the DAM Console.

---

## What `make setup` Does

The setup script runs automatically and handles:

1. **Python environment** — creates `.venv/` and installs all Python dependencies via `uv sync` (includes `dev`, `services`, and `torch` extras)
2. **Rust extension** — compiles `dam_rs` via `maturin` and installs it into `.venv/`
3. **Frontend** — runs `npm install` in `dam-console/` and creates `.env.local` if it doesn't exist

If `uv` is not installed, the script installs it automatically. `cargo` must be available beforehand — install it with:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

---

## Hardware Support (SO-ARM101 / LeRobot)

For physical robot hardware, use the lerobot variant:

```bash
make setup-lerobot   # adds lerobot + cv2 hardware extras
make run
```

Running `make setup` after `make setup-lerobot` will preserve the hardware extras — it detects lerobot in the existing venv and keeps it.

---

## Useful Make Targets

| Target | Description |
|--------|-------------|
| `make setup` | First-time setup: venv + Rust + npm |
| `make setup-lerobot` | Setup with SO-ARM101 hardware support |
| `make run` | Start backend + console (follows `.dam_stackfile.yaml`) |
| `make test` | Run full test suite (Python + Rust + frontend) |
| `make build-rs` | Rebuild Rust extension only |
| `make lint` | Run linters (ruff, mypy, cargo clippy) |
| `make clean` | Remove `.venv/`, Rust build artefacts, `node_modules/` |

---

## Verify

After setup completes, verify the installation:

```bash
# Python package
.venv/bin/python -c "import dam; print(dam.__version__)"

# Rust extension
.venv/bin/python -c "import dam_rs; print('Rust data plane OK')"

# Run the test suite
make test
```
