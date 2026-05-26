<div align="center">

<h1>Detachable Action Monitor (DAM)</h1>

A safety layer between your ML policy and robot hardware.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python)](https://www.python.org/downloads/)
[![Rust 1.80+](https://img.shields.io/badge/rust-1.80%2B-orange?logo=rust)](https://www.rust-lang.org/)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen)](LICENSE)
[![Discussions](https://img.shields.io/badge/Chat-GitHub_Discussions-blue?logo=github)](https://github.com/ez945y/DAM/discussions)

[What It Does](#what-it-does) · [Code Example](#see-it-in-code) · [Quick Start](#quick-start) · [Architecture](#architecture) · [Docs](#documentation)
</div>


https://github.com/user-attachments/assets/a10711ea-a419-4aee-ba06-de1e2d437d49


## What It Does

DAM intercepts every action your policy proposes and evaluates it through a layered guard pipeline before it reaches hardware. Each action is either **passed**, **clamped** (modified to be safe), or **rejected** — without touching your policy weights or hardware drivers.

```
Policy ──▶ [ L0: OOD ] ──▶ [ L1: Kinematics ] ──▶ [ L2: Task ] ──▶ [ L3: Hardware ] ──▶ Robot
              │                    │                    │                   │
         Is this input         Can this move        Does this make      Is the hardware
         in-distribution?      physically happen?   sense for the task? healthy?
```

**Why this exists:** ML policies can propose unsafe actions — out-of-distribution inputs, joint limits exceeded, task logic violated, or hardware overheating. DAM makes those safety boundaries explicit, configurable, and inspectable.

> **Important:** DAM is experimental research software. It is not certified for safety-critical or production use. Safety is genuinely hard — we provide tools to help define and enforce boundaries, but cannot guarantee they will catch every failure mode. Use it as one layer of defense, not the only one.

---

## See It in Code

A guard is a Python class with a single `check()` method:

```python
import numpy as np
import dam
from dam import Guard, GuardResult, Observation, ActionProposal, ValidatedAction

@dam.guard("L1")
class JointLimitGuard(Guard):
    expected_decisions = frozenset({dam.GuardDecision.PASS, dam.GuardDecision.CLAMP})

    def __init__(self, limit: float = 1.0):
        self.limit = limit

    def check(self, obs: Observation, action: ActionProposal, **kwargs) -> GuardResult:
        target = action.target_joint_positions
        if np.all(np.abs(target) <= self.limit):
            return GuardResult.pass_result(self.get_name(), self.get_layer())

        clamped = np.clip(target, -self.limit, self.limit)
        return GuardResult.clamp(
            ValidatedAction(target_joint_positions=clamped),
            self.get_name(), self.get_layer(),
            reason=f"Clamped to [{-self.limit}, {self.limit}]",
        )
```

Run it without any server or hardware:

```bash
python examples/hello_guard.py
# Safe:      PASS  reason=''
# Dangerous: CLAMP  reason='Clamped joints to [-1.0, 1.0]'
```

More examples in [`examples/`](examples/): custom callbacks, minimal Stackfiles, and full robot configs.

---

## Quick Start

```bash
git clone https://github.com/ez945y/DAM.git
cd DAM
make setup    # Python venv + Rust extension + npm install (~3 min)
make run      # Backend :8080 + Console :3000
```

Open **http://localhost:3000** to see the DAM Console. The demo Stackfile replays a dataset through the full guard pipeline — you can see real guard decisions without hardware.

For the step-by-step walkthrough, see the [Quick Start guide](docs/getting-started/quickstart.md).

### CLI

After `make setup`, the `dam` CLI is available:

```bash
dam doctor                                # check environment
dam callbacks                             # list 18 built-in safety checks
dam validate examples/stackfiles/*.yaml   # schema-check Stackfiles
dam run examples/stackfiles/demo.yaml --cycles 200 --task demo
dam replay <session>.mcap                 # summarize a recorded session
```

### Make Targets

| Command | What it does |
|---------|-------------|
| `make setup` | First-time install (venv + Rust + npm) |
| `make run` | Backend + pre-built frontend |
| `make dev` | Backend + frontend with hot-reload |
| `make test` | Full test suite (668+ Python tests + 109 frontend tests) |
| `make validate` | Validate example Stackfiles |

---

## Architecture

<img src="docs/diagrams/diagram1_system_architecture.png" alt="System Architecture" width="600" />

### Guard Layers

| Layer | What it checks | Example checks |
|-------|---------------|----------------|
| **L0** OOD Detection | Is the observation in-distribution? | Real-NVP / Memory Bank anomaly scoring |
| **L1** Physical Kinematics | Can this move physically happen? | Joint limits, workspace bounds, velocity caps |
| **L2** Task Execution | Does this make sense for the task? | Gripper sequence, progress enforcement |
| **L3** Hardware Monitoring | Is the hardware healthy? | Temperature, current, voltage, heartbeat |

The final decision is the **most restrictive** outcome across all active layers.

### Stackfile Configuration

Everything is configured in a YAML Stackfile — boundaries, fallbacks, tasks, hardware, and policy:

```yaml
boundaries:
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]

  temperature_limit:
    layer: L3
    type: single
    nodes:
      - callback: temperature_limit
        fallback: slow_down
        params:
          max_temperature_c: 55.0
```

Start from [`examples/stackfiles/minimal.yaml`](examples/stackfiles/minimal.yaml) (smallest valid config) or see the [Stackfile Walkthrough](docs/getting-started/stackfile-walkthrough.md).

### 18 Built-in Safety Checks

List them with `dam callbacks`:

- **L0:** OOD detector with selectable backends (Real-NVP, Memory Bank, Welford)
- **L1:** Joint position/velocity limits, workspace bounds, keep-out zones, orientation, geofence, Cartesian velocity, smoothness
- **L2:** Task gripper sequence enforcement
- **L3:** Temperature, current, voltage, force/torque, hardware watchdog, host health

### Fallback State Machine

When a guard rejects an action, the runtime pushes a fallback context onto a severity-ordered stack. Each boundary specifies its own fallback strategy, and fallbacks auto-escalate if the trigger doesn't clear:

```
Normal → SlowDown → HoldPosition → SafeRetreat → EmergencyStop
```

<img src="docs/diagrams/diagram2_runtime_workflow.png" alt="Runtime Workflow" width="600" />

---

## Key Capabilities

- **YAML-driven** — adjust safety boundaries without code changes
- **MCAP recording** — every observation, action, and guard decision is logged for replay and post-hoc analysis
- **Real-time console** — cycle-by-cycle inspection of guard decisions, latency, and risk
- **Adapter isolation** — swap between LeRobot, ROS 2, dataset replay, or custom adapters
- **Experiment harness** — reproducible evaluations (RQ1-RQ5) with cached artifacts

---

## Documentation

| Goal | Start here |
|------|------------|
| Learn DAM step by step | [Learn DAM](docs/learn/index.md) |
| Read a Stackfile | [Stackfile Walkthrough](docs/getting-started/stackfile-walkthrough.md) |
| Make safe config edits | [Common Stackfile Edits](docs/getting-started/common-stackfile-edits.md) |
| Understand the console | [Console Walkthrough](docs/getting-started/console-walkthrough.md) |
| Prepare for hardware | [Hardware Readiness](docs/getting-started/hardware-readiness.md) |
| Fix first-run issues | [Troubleshooting](docs/getting-started/troubleshooting.md) |

```bash
make docs   # preview locally
```

---

## Project Structure

```
dam/                    # Python core — guard pipeline, runtime, services
dam-console/            # Next.js dashboard (TypeScript + React)
dam-rust/               # Rust extension for high-throughput MCAP recording
examples/               # Runnable examples and Stackfile templates
  hello_guard.py        #   ← start here: minimal guard in 20 lines
  custom_callback.py    #   ← write your own boundary callback
  stackfiles/           #   ← YAML configs from minimal to full robot
tests/                  # Unit, integration, safety, and property tests
docs/                   # MkDocs documentation site
```

---

## Contributing

See [Contributing](docs/contributing.md) for setup and guidelines. We welcome help with:

- Safety testing and adversarial scenarios
- Real-time performance optimization
- Additional hardware adapters
- Documentation and example Stackfiles

---

## Research

DAM includes an experiment harness for reproducible evaluation (RQ1-RQ5), runnable from the console or CLI:

```bash
dam experiment list
dam experiment run l0-calibration
```

Results are cached — rerunning the same configuration reuses trained models and extracted features. See [Experiment Runners](docs/experiments.md) for details.
