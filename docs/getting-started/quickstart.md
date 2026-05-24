# Quick Start

Get DAM running without robot hardware, then confirm that the console, CLI, and demo Stackfiles are wired correctly. Budget about **10 minutes** on a fresh machine; repeat runs are faster.

## What Success Looks Like

By the end of this page you should have:

- A local Python environment and Rust extension built by `make setup`
- The backend API listening on `http://localhost:8080`
- The DAM Console available at `http://localhost:3000`
- Example Stackfiles passing validation
- A clear next page for learning guards, Stackfiles, or the console

---

## Installation & Setup

```bash
# Clone the repository
git clone https://github.com/ez945y/DAM.git
cd DAM

# One-time setup (Python venv + Rust extension + npm)
make setup
```

Expected result: setup finishes without errors and creates `.venv/`. It may take several minutes because it installs Python packages, builds the Rust extension, and installs console dependencies.

If setup fails because `cargo` is missing, install Rust from [rustup.rs](https://rustup.rs/) and run `make setup` again.

---

## Start DAM

```bash
# Start the backend + frontend
make run
```

This launches:
- **API server** at `http://localhost:8080`
- **DAM Console** at `http://localhost:3000`
- **Runtime host** using the repo's default Stackfile convention

---

## Confirm It Is Running

Open your browser:

- **Dashboard:** http://localhost:3000
- **API Docs:** http://localhost:8080/docs

Expected result: the dashboard loads and shows runtime status, cycle counters, risk state, guard decisions, and latency panels. If the console opens but looks disconnected, confirm that the API server is still running on port 8080.

## Validate A Demo Stack

Before editing a robot configuration, validate the example Stackfiles:

```bash
make validate
```

Expected result: every file under `examples/stackfiles/` reports `OK`.

Inspect one demo Stackfile to see what DAM resolved:

```bash
.venv/bin/dam inspect examples/stackfiles/demo.yaml
```

Expected result: the output lists safety settings, guards, boundaries, tasks, and fallback escalation. You do not need to understand every field yet; just confirm that the stack can be loaded and explained.

!!! note "No hardware path"
    The demo Stackfile is safe to inspect and validate without a robot. It uses a dataset source and a matching sink reference for replay-style development. The ACT policy may use CPU and may download model assets the first time it is run.

---

## Learn The First Workflow

Use this loop when you are learning DAM:

1. Pick an example Stackfile under `examples/stackfiles/`.
2. Run `make validate`.
3. Inspect it with `.venv/bin/dam inspect <path>`.
4. Start DAM with `make run`.
5. Open the console and identify whether the latest cycles are PASS, CLAMP, or REJECT.

The important habit is to validate configuration before thinking about hardware.

---

## Optional: Run Tests

```bash
# Full test suite (Python + Rust + Frontend)
make test

# Or test individually
make test-py      # Python only
make test-rs      # Rust only
make test-ui      # Frontend only
make lint         # Linters only
```

---

## Customize with Stackfiles

After the demo path works, edit a copy of an example Stackfile to change:

- Guard parameters (L0–L3)
- Task boundaries
- Hardware adapters
- Safety constraints

Then validate before running:

```bash
make validate
make run
```

---

## Available Commands

```bash
make help     # Show all available targets
```

| Command | Purpose |
|---------|---------|
| `make setup` | First-time setup (Python + Rust + npm) |
| `make setup-lerobot` | Setup with SO-ARM101 hardware support |
| `make run` | Start backend + frontend |
| `make test` | Run all tests + linters |
| `make clean` | Remove build artifacts |

---

## Next Steps

- **Follow the guided path** → [Learn DAM](../learn/index.md)
- **Read a Stackfile step by step** → [Stackfile Walkthrough](stackfile-walkthrough.md)
- **Learn the concepts** → [Architecture Overview](../concepts/architecture.md)
- **Understand guards** → [Guard Stack Explained](../concepts/guards-explained.md)
- **Design boundaries** → [Boundary System](../concepts/boundaries.md)
- **Full learning path** → [Complete Tutorial](../learn/tutorial.md)
- **Look up terms** → [Glossary](../learn/glossary.md)

---

You are running DAM. Next, use the console to connect what the runtime is doing with the Stackfile that configured it.
