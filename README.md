<div align="center">

<h1>Detachable Action Monitor (DAM)</h1>

Detachable Trust. Deterministic Safety.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python)](https://www.python.org/downloads/)
[![Rust 1.80+](https://img.shields.io/badge/rust-1.80%2B-orange?logo=rust)](https://www.rust-lang.org/)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen)](LICENSE)
[![Discussions](https://img.shields.io/badge/Chat-GitHub_Discussions-blue?logo=github)](https://github.com/ez945y/DAM/discussions)

[Features](#features) • [Quick Start](#quick-start) • [Documentation](#documentation) • [Configuration](#configuration)
</div>


https://github.com/user-attachments/assets/a10711ea-a419-4aee-ba06-de1e2d437d49



**DAM** is a detachable safety middleware that sits between any machine learning policy (or controller) and robot hardware. It intercepts every proposed action, evaluates it through a layered guard stack (L0–L3), and either **passes**, **clamps**, or **rejects** it — without modifying the policy weights or hardware drivers.

This design enables strong safety boundaries while keeping the learning/policy layer fully detachable and upgradable.

### Features
+ **Graduated L0→L3 Pipeline**: Validates OOD, motion feasibility, task logic, and hardware health with graceful fallback escalation instead of hard crashes.
+ **L1 Motion Boundary (CBF)**: Solves joint, velocity, and workspace constraints in one ProxSuite QP for minimally disruptive safe actions.
+ **MCAP Loopback Ring Buffer**: Saves ±30s of frames, observations, and safety events around failures into MCAP using Rust async workers.
+ **Cycle Inspector**: Audits each control loop with per-guard latency, boundary decisions, and observation/action diffs in-browser.
+ **Rust Data Plane**: Moves serialization, MCAP writing, and messaging out of the Python GIL for deterministic performance.
+ **Strict Adapter Isolation**: Sensor, Policy, and Action ABCs enable swapping LeRobot, ROS 2, or OpenCV without touching guard logic.
7. **Stackfile-Driven Config**: Robots, policies, and safety boundaries are fully YAML-defined and CI-validated via `dam validate`.

**Disclaimer**: DAM is experimental research software and not certified for safety-critical or production environments.


---

### Quick Start

```bash
git clone https://github.com/ez945y/DAM.git
cd DAM
make setup
make run
```

Open **http://localhost:3000** for the DAM Console and **http://localhost:8080/docs** for API docs.

For the guided no-hardware path, see [Quick Start](docs/getting-started/quickstart.md).

| Command      | Description                                              |
|--------------|----------------------------------------------------------|
| `make setup` | Create venv, compile Rust extension, install dependencies |
| `make run`   | Start backend (:8080) + frontend (:3000)                |
| `make validate` | Validate example Stackfiles                          |
| `make docs-check` | Run strict MkDocs and documentation pattern checks |
| `make test`  | Run full test suite (unit + integration + safety)       |
| `make clean` | Remove build artifacts                                  |

The `dam` CLI is available after `make setup`:

```bash
.venv/bin/dam doctor                                # check environment / dependencies
.venv/bin/dam callbacks                             # list built-in boundary callbacks
.venv/bin/dam inspect <stack>                       # print the resolved Stackfile graph
.venv/bin/dam validate examples/stackfiles/*.yaml   # schema-check Stackfiles (CI gate)
.venv/bin/dam run <stack> --cycles 200              # headless control loop
.venv/bin/dam replay <session>.mcap                 # summarise a recorded session
.venv/bin/dam help [command]
```

After starting, open **http://localhost:3000** in your browser and select a configuration template:

- **Quick Start** — Simulation only (no hardware needed)
- **SO-ARM101** — Pre-configured for SO-ARM101 robot
- **Custom** — Create your own Stackfile

---

### Documentation

| Goal | Start here |
|------|------------|
| Learn DAM step by step | [Learn DAM](docs/learn/index.md) |
| Read a Stackfile | [Stackfile Walkthrough](docs/getting-started/stackfile-walkthrough.md) |
| Make safe config edits | [Common Stackfile Edits](docs/getting-started/common-stackfile-edits.md) |
| Understand the console | [Console Walkthrough](docs/getting-started/console-walkthrough.md) |
| Prepare for hardware | [Hardware Readiness](docs/getting-started/hardware-readiness.md) |
| Fix first-run issues | [Troubleshooting](docs/getting-started/troubleshooting.md) |

Preview the MkDocs site locally:

```bash
make docs
```

---

### Configuration

#### System Architecture

<img src="docs/diagrams/diagram1_system_architecture.png" alt="System Architecture" width="600" />

#### Workflow

<img src="docs/diagrams/diagram2_runtime_workflow.png" alt="Runtime Workflow" width="600" />


**Guard Layers**

| Layer | Name                    | Responsibility                                      |
|-------|-------------------------|-----------------------------------------------------|
| L0    | OOD Detection           | Out-of-distribution observation detection           |
| L1    | Physical Kinematics     | Joint limits, workspace, velocity                   |
| L2    | Task Execution          | Mission progress and boundary enforcement           |
| L3    | Hardware Monitoring     | Temperature, current, voltage, heartbeat            |

The final decision is the **most restrictive** outcome from all active layers.

---

### Contributing

See [Contributing](docs/contributing.md) for details on:
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
