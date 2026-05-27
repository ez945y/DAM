<div align="center">

<h1>Detachable Action Monitor (DAM)</h1>

See where your policy breaks — before your hardware does.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python)](https://www.python.org/downloads/)
[![Rust 1.80+](https://img.shields.io/badge/rust-1.80%2B-orange?logo=rust)](https://www.rust-lang.org/)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen)](LICENSE)
[![Discussions](https://img.shields.io/badge/Chat-GitHub_Discussions-blue?logo=github)](https://github.com/ez945y/DAM/discussions)

[Quick Start](#quick-start) · [Safe Recording](#safe-recording-for-imitation-learning) · [Architecture](#architecture) · [Docs](#documentation)
</div>


https://github.com/user-attachments/assets/a10711ea-a419-4aee-ba06-de1e2d437d49


## What It Does

ML policies always output the next action — they have no idea when something is unsafe. DAM intercepts every action and evaluates it through a layered guard pipeline before it reaches hardware. Each action is either **passed**, **clamped** (adjusted to stay safe), or **rejected**.

The goal isn't to hide failures. It's to make them visible and easier to understand.

<img src="docs/diagrams/diagram1_system_architecture.png" alt="System Architecture" width="700" />

> **Important:** DAM is experimental research software, not a certified safety system.

---

## Quick Start

```bash
git clone https://github.com/ez945y/DAM.git
cd DAM
make setup    # Python venv + Rust extension + npm install (~3 min)
make run      # Backend :8080 + Console :3000
```

Open **http://localhost:3000** — the demo Stackfile replays a dataset through the full guard pipeline without hardware.

### CLI

```bash
.venv/bin/dam doctor                                # check environment
.venv/bin/dam callbacks                             # list 18 built-in safety checks
.venv/bin/dam validate examples/stackfiles/*.yaml   # schema-check Stackfiles
.venv/bin/dam run examples/stackfiles/demo.yaml --cycles 200 --task demo
```

### Make Targets

| Command | What it does |
|---------|-------------|
| `make setup` | First-time install (venv + Rust + npm) |
| `make run` | Backend + pre-built frontend |
| `make dev` | Backend + frontend with hot-reload |
| `make test` | Full test suite (688+ Python tests + 109 frontend tests) |
| `make record` | Safe IL recording with DAM guards |
| `make callbacks` | List all 18 built-in safety checks |
| `make validate` | Validate example Stackfiles |

---

## Safe Recording for Imitation Learning

During data collection, DAM smooths motions and catches bad data before it enters your dataset. Integrates with [LeRobot](https://github.com/huggingface/lerobot) — one file configures hardware, safety boundaries, and recording.

```bash
make record                                # reads examples/stackfiles/safety.yaml
make record ARGS="--dataset.num_episodes=20"   # CLI args override YAML
```

### Python API (3 levels)

```python
import dam

# Level 1: one-liner (notebooks)
safe_action = dam.safe(action, obs, stackfile="safety.yaml")

# Level 2: stateful guard (recording loops)
guard = dam.SafetyGuard("safety.yaml", task="record")
safe_action = guard(action, obs)          # dict in → dict out, ndarray in → ndarray out

# Level 3: lerobot pipeline (one line addition)
from dam import SafetyProcessorStep
robot_action_processor.steps.insert(0, SafetyProcessorStep("safety.yaml"))
```

<img src="docs/diagrams/diagram2_runtime_workflow.png" alt="Runtime Workflow" width="600" />

The recorded dataset contains **only safe actions** — your policy trains on data that already respects physical constraints. See [Safe Recording Guide](docs/getting-started/safe-recording.md) for full details.

---

## Architecture

### Guard Layers

| Layer | What it checks | Example checks |
|-------|---------------|----------------|
| **L0** OOD Detection | Is the observation in-distribution? | Real-NVP / Memory Bank anomaly scoring |
| **L1** Physical Kinematics | Can this move physically happen? | Joint limits, workspace bounds, velocity caps |
| **L2** Task Execution | Does this make sense for the task? | Gripper sequence, progress enforcement |
| **L3** Hardware Monitoring | Is the hardware healthy? | Temperature, current, voltage, heartbeat |

L0 and L1 run **in parallel**. The final decision is the **most restrictive** outcome across all active layers.

### Stackfile Configuration

Everything is configured in a YAML Stackfile:

```yaml
boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.8243, 1.7691, 1.8326, 1.8067, 3.0741, 1.7453]
          lower: [-1.8243, -1.7691, -1.8326, -1.8067, -3.0741, 0.0]
```

Start from [`examples/stackfiles/minimal.yaml`](examples/stackfiles/minimal.yaml) or see the [Stackfile Walkthrough](docs/getting-started/stackfile-walkthrough.md).

### 18 Built-in Safety Checks

Run `dam callbacks` to list them all:

- **L0:** OOD detector (Real-NVP, Memory Bank, Welford)
- **L1:** Joint position/velocity limits, workspace bounds, keep-out zones, orientation, geofence, Cartesian velocity, smoothness
- **L2:** Task gripper sequence enforcement
- **L3:** Temperature, current, voltage, force/torque, hardware watchdog, host health

---

## Documentation

| Goal | Start here |
|------|------------|
| Learn DAM step by step | [Learn DAM](docs/learn/index.md) |
| Read a Stackfile | [Stackfile Walkthrough](docs/getting-started/stackfile-walkthrough.md) |
| Common config edits | [Common Stackfile Edits](docs/getting-started/common-stackfile-edits.md) |
| Understand the console | [Console Walkthrough](docs/getting-started/console-walkthrough.md) |
| Prepare for hardware | [Hardware Readiness](docs/getting-started/hardware-readiness.md) |
| Troubleshooting | [Troubleshooting](docs/getting-started/troubleshooting.md) |

---

## Contributing

See [Contributing](docs/contributing.md). We welcome safety testing, hardware adapters, performance optimization, and example Stackfiles.
