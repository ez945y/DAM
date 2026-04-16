# Detachable Action Monitor (DAM)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python)](https://www.python.org/downloads/)
[![Rust 1.80+](https://img.shields.io/badge/rust-1.80%2B-orange?logo=rust)](https://www.rust-lang.org/)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen)](LICENSE)
[![Discussions](https://img.shields.io/badge/Chat-GitHub_Discussions-blue?logo=github)](https://github.com/ez945y/DAM/discussions)

### Detachable Trust. Deterministic Safety.
**DAM** is a detachable safety middleware that sits between any machine learning policy (or controller) and robot hardware. It intercepts every proposed action, evaluates it through a layered guard stack (L0–L4), and either **passes**, **clamps**, or **rejects** it — without modifying the policy weights or hardware drivers.

This design enables strong safety boundaries while keeping the learning/policy layer fully detachable and upgradable.

### Key Features

- **5-Layer Guard Stack**: Progressive defense from perception (L0) → hardware (L4)
- **Rust Data Plane**: Deterministic, real-time-safe execution outside the Python GIL
- **Stackfile-Driven**: Swap hardware, policies, or safety rules via YAML. Zero Python code for simple tasks.
- **Hot-Reload Boundaries**: Update safety constraints without stopping the robot
- **Fail-to-Reject**: Any guard timeout, crash, or exception → immediate rejection
- **MCAP Loopback Buffer**: Capture ±30s of context around safety events for analysis
- **Built-in Adapters**: LeRobot (SO-ARM101) and ROS 2 support

---

### Safety Guarantees

DAM follows **defense-in-depth** and **fail-safe** design principles.

- **Fail-to-Reject**: Any timeout, exception, or unexpected behavior in the guard stack results in immediate action rejection.
- **Rust Data Plane**: Memory-safe, deterministic execution with no data races. The core validation path runs in a real-time-friendly Rust layer.
- **Layered Verification**:
  - L0: Statistical/ML-based out-of-distribution detection
  - L1: Shadow physics simulation (preflight checks)
  - L2: Kinematic, dynamic, and workspace feasibility
  - L3: Task-level logical consistency and mission progress
  - L4: Hardware health monitoring (motors, temperature, watchdogs)
- **Observability**: Full MCAP recording of sensor streams and guard decisions around violations.
- **Hot-Reload Safety**: All boundary updates are validated before activation.

**Important Disclaimer**:  
DAM is currently **research and experimental-grade software**. It is **not certified** for safety-critical or production use in human-collaborative or high-risk environments. Use at your own risk. We are actively working toward formal verification, worst-case timing analysis, and compliance-oriented documentation.

---

### Quick Start

```bash
git clone https://github.com/ez945y/DAM.git
cd DAM
make setup
make run
```

| Command      | Description                                              |
|--------------|----------------------------------------------------------|
| `make setup` | Create venv, compile Rust extension, install dependencies |
| `make run`   | Start backend (:8080) + frontend (:3000)                |
| `make test`  | Run full test suite (unit + integration + safety)       |                       |
| `make clean` | Remove build artifacts                                  |

After starting, open **http://localhost:3000** in your browser and select a configuration template:

- **Quick Start** — Simulation only (no hardware needed)
- **SO-ARM101** — Pre-configured for SO-ARM101 robot
- **Custom** — Create your own Stackfile

---

### Architecture

DAM acts as a transparent safety layer:

```
Policy / Controller
        │
        ▼
Proposed Action  ──────▶  [ Guard Stack L0–L4 ]  ──────▶  Validated Action
        ▲                       │      │      │                  │
        │                       │      │      │                  ▼
Observations & State  ─────────┘      │      └──────────▶  Fallback (Hold / Retreat / E-Stop)
                                       │
                                       ▼
                                 Decision: Pass / Clamp / Reject
```

**Guard Layers**

| Layer | Name                    | Responsibility                                      | Status      |
|-------|-------------------------|-----------------------------------------------------|-------------|
| L0    | OOD Detection           | Out-of-distribution observation detection           | Available   |
| L1    | Preflight Simulation    | Shadow physics simulation and prediction            | In Progress |
| L2    | Motion Safety           | Joint limits, workspace, velocity & dynamics        | Available   |
| L3    | Task Execution          | Mission progress and logical consistency            | In Progress |
| L4    | Hardware Monitoring     | Motor status, temperature, watchdogs                | Available   |

The final decision is the **most restrictive** outcome from all active layers.

---

### Project Layout

```
dam/                  # Python Control Plane
  adapter/            # Hardware adapters (LeRobot, ROS 2)
  guard/              # L0–L4 guard implementations
  config/             # Stackfile parser & hot-reload logic
  runtime/            # Main control loop
  types/              # Core data structures

dam-rust/             # Rust Data Plane (deterministic core)
  dam-py/             # PyO3 bindings

examples/             # Demo Stackfiles and configurations
tests/                # Unit, integration, and safety tests
docs/                 # Architecture and safety documentation
```

---

### Documentation

- [docs/DAM_Specification.md](docs/DAM_Specification.md) — Full architecture and safety reference
- [docs/installation.md](docs/installation.md) — Detailed installation guide
- [CONTRIBUTING.md](CONTRIBUTING.md) — Development setup and contribution guidelines

---

### Roadmap

**v0.2.0 (Current focus)**
- Complete ROS 2 adapter
- Finish L3 (Task Execution) and L4 (Hardware Monitoring)
- Problem isolation and debugging tools

**v0.3.0**
- Mature L1 Preflight Simulation with physics engine
- Formal safety specifications and threat modeling
- Detailed performance benchmarks (latency, throughput, WCET)

**v0.4.0**
- More built-in boundary types
- Domain-specific bundles (manipulation, mobile manipulation, etc.)
- Extensive adversarial testing suite

**Longer term**
- Formal verification of critical safety paths
- Support for additional robot platforms
- Certification preparation artifacts

---

### License

[Mozilla Public License 2.0](LICENSE) — Contributions welcome!

**Note**: Given the safety-critical nature of this project, we strongly encourage all contributions to include thorough testing and documentation.

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on:
- Setting up the development environment
- Code style and testing requirements
- How to propose new features or guard layers

We especially welcome help in the following areas:
- Safety testing and adversarial scenario development
- Real-time performance optimization
- Additional hardware adapters
- Documentation and example Stackfiles

---

**DAM aims to make advanced robot safety modular, verifiable, and accessible to the embodied AI community.**

Feedback and discussions are highly encouraged in [GitHub Discussions](https://github.com/ez945y/DAM/discussions).

---

*Built for safer embodied AI.*