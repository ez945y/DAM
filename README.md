<div align="center">

<h1>DAM — Detachable Action Monitor</h1>

See where your policy breaks — before your hardware does.

[![PyPI](https://img.shields.io/pypi/v/robot-dam?logo=pypi&logoColor=white)](https://pypi.org/project/robot-dam/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](https://www.python.org/downloads/)
[![Rust 1.80+](https://img.shields.io/badge/rust-1.80%2B-orange?logo=rust)](https://www.rust-lang.org/)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen)](LICENSE)
[![Discussions](https://img.shields.io/badge/Chat-GitHub_Discussions-blue?logo=github)](https://github.com/ez945y/DAM/discussions)

[Install](#install) · [Quick Start](#quick-start) · [Isaac Sim](#isaac-sim-integration) · [Architecture](#architecture) · [Docs](#documentation)
</div>


https://github.com/user-attachments/assets/a10711ea-a419-4aee-ba06-de1e2d437d49


## Install

```bash
pip install robot-dam            # includes torch, lerobot, mcap, dam-rs (Rust extension)
pip install robot-dam[isaac]     # + Isaac Sim 6.0 adapter
pip install robot-dam[full]      # + ROS2, REST API, QP solver, everything
```

Or from source:

```bash
git clone https://github.com/ez945y/DAM.git && cd DAM
make setup
```

---

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

# One input dict per cycle: "action" is the command to validate, every other
# key is an observation group. Returns the safe command in the action's form.

# Level 1: one-liner (notebooks)
safe_action = dam.guardrail({"joints": obs, "action": action}, stackfile="safety.yaml")

# Level 2: stateful guard (recording loops + Isaac Sim)
guard = dam.Guardrail("safety.yaml", task="record")   # prints the obs/action contract
safe_action = guard({"joints": obs, "action": action})  # mirrors the action's type

# Level 3: lerobot pipeline (one line addition)
from dam import GuardrailProcessorStep
robot_action_processor.steps.insert(0, GuardrailProcessorStep("safety.yaml"))
```

<img src="docs/diagrams/diagram2_runtime_workflow.png" alt="Runtime Workflow" width="600" />

The recorded dataset contains **only safe actions** — your policy trains on data that already respects physical constraints. See [Safe Recording Guide](https://ez945y.github.io/DAM/getting-started/safe-recording) for full details.

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

Start from [`examples/stackfiles/minimal.yaml`](examples/stackfiles/minimal.yaml) or see the [Stackfile Walkthrough](https://ez945y.github.io/DAM/getting-started/stackfile-walkthrough/).

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
| Learn DAM step by step | [Learn DAM](https://ez945y.github.io/DAM/learn/) |
| Read a Stackfile | [Stackfile Walkthrough](https://ez945y.github.io/DAM/getting-started/stackfile-walkthrough/) |
| Common config edits | [Common Stackfile Edits](https://ez945y.github.io/DAM/getting-started/common-stackfile-edits/) |
| Understand the console | [Console Walkthrough](https://ez945y.github.io/DAM/getting-started/console-walkthrough/) |
| Prepare for hardware | [Hardware Readiness](https://ez945y.github.io/DAM/getting-started/hardware-readiness/) |
| Troubleshooting | [Troubleshooting](https://ez945y.github.io/DAM/getting-started/troubleshooting/) |

---

## Contributing

See [Contributing](https://ez945y.github.io/DAM/contributing/). We welcome safety testing, hardware adapters, performance optimization, and example Stackfiles.
