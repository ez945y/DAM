
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