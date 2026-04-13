# Quick Start

Get DAM running in **2 minutes**. Clone the repo and use the Makefile.

---

## Installation & Setup

```bash
# Clone the repository
git clone https://github.com/ez945y/DAM.git
cd DAM

# One-time setup (Python venv + Rust extension + npm)
make setup
```

Done! You now have:
- ✅ Python 3.11+ with all dependencies
- ✅ Rust data plane compiled
- ✅ Frontend console ready

---

## Run DAM

```bash
# Start the backend + frontend
make run
```

This launches:
- **DAM Runtime** — validates every robot action through the guard stack
- **Web Console** — real-time dashboard at `http://localhost:3000`
- **API Server** — REST + WebSocket at `http://localhost:8080`

---

## See It In Action

Open your browser:

- **Dashboard:** http://localhost:3000
- **API Docs:** http://localhost:8080/docs

You'll see live telemetry: cycles, risk levels, guard decisions, latencies.

---

## Run Tests

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

Edit `.dam_stackfile.yaml` to change:
- Guard parameters (L0–L4)
- Task boundaries
- Hardware adapters
- Safety constraints

Then:

```bash
make run  # Reload automatically
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

- **Learn the concepts** → [Architecture Overview](../concepts/architecture.md)
- **Understand guards** → [Guard Stack Explained](../concepts/guards-explained.md)
- **Design boundaries** → [Boundary System](../concepts/boundaries.md)
- **Full learning path** → [Complete Tutorial](../learn/tutorial.md)
- **Look up terms** → [Glossary](../learn/glossary.md)

---

**You're running DAM! 🚀 Next: explore the console at http://localhost:3000**
