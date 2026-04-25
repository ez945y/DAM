
# Contributing to Detachable Action Monitor (DAM)

Thank you for your interest in contributing to **DAM**!
We welcome contributions from researchers, engineers, students, and anyone passionate about making robot learning safer and more reliable.

---

## How to Contribute

### 1. Development Setup

```bash
git clone https://github.com/ez945y/DAM.git
cd DAM
make setup

# Start the dev server (backend + Next.js hot-reload)
make dev
```

This will:
- Create a Python virtual environment
- Compile the Rust extension (`dam-rust`)
- Install all required dependencies

Other useful commands:

| Command | Purpose |
|---------|---------|
| `make dev` | Hot-reload dev server (backend + Next.js) |
| `make run` | Production mode (build frontend + start backend) |
| `make test` | Run full test suite |
| `make docs` | Preview documentation at http://localhost:8002 |

Make sure you have:
- Python 3.12 or higher
- Rust 1.80 or higher (with `rustup`)
- `make` (or manually run the commands in `Makefile`)

### Troubleshooting

#### Python Environment Issues

**ImportError: No module named 'dam_rs'**

If you see this error when running DAM, it means the Rust extension isn't installed in the active venv. This can happen if:
- You upgraded Python system-wide (e.g., via Homebrew or conda)
- The venv was corrupted

Fix:
```bash
# Clean the old venv and reinstall
rm -rf .venv
make setup
```

**msgpack not found in production run**

If MCAP sessions fail to parse with "No module named 'msgpack'", ensure `msgpack` is in your dependencies:

```bash
# Check it's installed
.venv/bin/python -c "import msgpack"

# If not, install it
.venv/bin/python -m pip install msgpack
```

The `msgpack` package is required for deserializing cycle records stored in MCAP files.

#### Understanding the Rust Extension (`dam_rs`)

DAM uses a **Rust extension** (`dam_rs`) for high-performance MCAP writing:

- **Why Rust?** MCAP file I/O (recording every control cycle) happens in the hot path. Python's `mcap` library would block the control loop. Rust handles this asynchronously in a background thread.

- **What it provides**:
  - `McapWriter`: Async writer with background thread - write calls return in < 10µs
  - `ImageWriter`: Parallel JPEG encoding using Rayon thread pool

- **Installation**: `make setup` compiles this automatically and installs it into `.venv`:
  ```bash
  cd dam-rust/dam-py
  maturin build --release --interpreter .venv/bin/python
  uv pip install -e . --quiet
  ```

- **Troubleshooting "dam_rs not installed"**:
  ```bash
  # Check where it's installed
  .venv/bin/python -c "import dam_rs; print(dam_rs.__file__)"

  # If it's in conda instead, uninstall from conda:
  pip uninstall dam-rs  # from conda env

  # Then reinstall:
  make setup
  ```

#### Rust Build Issues

**Python version mismatch (PyO3 maximum version error)**

If you see `error: the configured Python interpreter version (3.14) is newer than PyO3's maximum supported version`:
1. Remove any system Python 3.14 from PATH
2. Clean rebuild:

```bash
rm -rf .venv dam-rust/target
make setup
```

**Python interpreter mismatch**

If `pip list | grep dam` shows `dam-rs` installed in the conda environment instead of `.venv`:
- Uninstall from conda: `conda uninstall dam-rs`
- Then run `make setup` again

#### Port Already in Use

If you see `OSError: [Errno 48] error while attempting to bind on address... address already in use`:
```bash
# Find and kill the existing process
lsof -i :8080
pkill -f dam_host
```

**Semaphore Warning (`dam_host.py`)**: You may see a "leaked semaphore" warning on shutdown; this is a known false positive from the hardware stack (LeRobot) that does not affect functionality.

### 2. Code Style & Quality

- **Python**: Follow [PEP 8](https://peps.python.org/pep-0008/) and use `ruff` + `black` for formatting.
- **Rust**: Follow the official Rust style (use `cargo fmt` and `cargo clippy`).
- All code must pass linting and type checking before submission.

Run the full check locally:
```bash
make test
# or individually:
make lint
make typecheck
```

### 3. Testing

We maintain three levels of tests:
- **Unit tests** – for individual guards and utilities
- **Integration tests** – for the full control loop and adapters
- **Safety tests** – adversarial scenarios and edge cases

Please add or update tests when introducing new features or modifying guard logic.

```bash
make test
```

### 4. Proposing New Features or Guard Layers

1. Open a **Discussion** in [GitHub Discussions](https://github.com/ez945y/DAM/discussions) first to discuss the idea.
2. If approved, create a detailed **Issue** describing:
   - The problem or proposed improvement
   - Design rationale
   - Safety implications
   - Planned implementation approach
3. Implement the changes in a feature branch and open a Pull Request.

---

## Areas Where We Especially Need Help

We are actively looking for contributions in the following areas:

- **Safety testing & adversarial scenario development**
  (Creating challenging edge cases and stress tests)

- **Real-time performance optimization**
  (Reducing latency and improving worst-case execution time in the Rust data plane)

- **Additional hardware adapters**
  (Support for new robot platforms beyond LeRobot and ROS 2)

- **Documentation & example Stackfiles**
  (Improving guides, tutorials, and providing ready-to-use configurations)

- **L1 Preflight Simulation**
  (Integrating and maturing the shadow physics engine)

- **Formal verification and threat modeling**
  (Helping move toward stronger safety guarantees)

---

## Pull Request Guidelines

- Keep PRs focused on a single change
- Include clear description and motivation
- Add or update tests as appropriate
- Update documentation when behavior changes
- All PRs must pass CI checks and receive at least one approval

**Important Note**: Because DAM is a safety-critical project, we place very high emphasis on correctness, test coverage, and clear documentation. Contributions that affect the guard stack or safety logic will receive extra scrutiny.

---

## Questions or Ideas?

- Join the conversation in [GitHub Discussions](https://github.com/ez945y/DAM/discussions)
- Open an Issue for bugs or feature requests

We appreciate every contribution — big or small — that helps make embodied AI safer.

---

**Thank you for helping build safer robot systems!**
*Built for safer embodied AI.*
