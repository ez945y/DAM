# Architecture Overview

DAM is designed as a **transparent safety middleware** that intercepts all policy outputs and validates them against a layered guard stack before hardware execution.

---

## High-Level Data Flow

```
┌─────────────────────┐
│  Observations       │  Sensor streams from hardware
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  ML Policy / Controller             │  Generates proposed actions
│  (PyTorch, Diffusion, ACT, etc.)    │
└──────────┬──────────────────────────┘
           │
           ▼ Proposed Action
┌─────────────────────────────────────┐
│  DAM Guard Stack (L0–L4)            │  Multi-layer safety filter
│  • L0: OOD Detection                │
│  • L1: Preflight Simulation         │  Decision:
│  • L2: Motion Safety                │  PASS / CLAMP / REJECT
│  • L3: Task Execution Logic         │
│  • L4: Hardware Health Monitor      │
└──────────┬──────────────────────────┘
           │
           ▼ Validated Action
┌─────────────────────┐
│  Fallback Engine    │  Hold / Retreat / E-Stop
│  (if rejected)      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Hardware Sinks                 │
│  • Motor controllers            │
│  • Gripper / end-effector       │
│  • Emergency stop circuits      │
└─────────────────────────────────┘
```

---

## System Components

### 1. **Control Plane** (Python)

The control plane coordinates lifecycle, Stackfile parsing, and hot-reload logic.

```python
# High-level entry point
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("mystack.yaml")
runtime.register_source("main", my_hardware_source)
runtime.register_policy(my_policy)
runtime.register_sink(my_hardware_sink)

runtime.start_task("mytask")
for _ in range(n_cycles):
    result = runtime.step()
```

**Responsibilities:**
- Parse and validate Stackfiles
- Manage component lifecycle (sources, sinks, guards, policies)
- Hot-reload boundary constraints without stopping the loop
- Collect telemetry and dispatch to logging

### 2. **Data Plane** (Rust – optional but recommended)

The data plane runs the real-time critical path: observation assembly, guard evaluation, and action dispatch.

```
Rust Layer (Real-time safe)
├── ObservationBus       — Zero-copy observation multiplexing
├── ActionBus            — Proposed → validated action pipeline
├── WatchdogTimer        — Per-cycle timeout enforcement
├── RiskController       — Windowed risk aggregation (Phase 2)
└── Guard Evaluators     — Vectorized constraint checking
```

**Why Rust?**
- **Memory safety** — no buffer overflows, use-after-free, or data races
- **Deterministic latency** — no garbage collection pauses
- **GIL-free** — multiple threads can run guards in parallel
- **Real-time friendly** — predictable worst-case execution time

Falls back to pure-Python if Rust extension is not compiled.

### 3. **Guard Stack** (Python + optional Rust acceleration)

Five independent layers evaluate the proposed action in sequence.

| Layer | Responsibility | Implementation |
|-------|-----------------|-----------------|
| **L0** | Detect out-of-distribution observations | Memory bank NN or Welford z-score |
| **L1** | Shadow physics simulation and prediction | Open-source physics engine (configurable) |
| **L2** | Joint limits, workspace, velocity & dynamics | Vectorized constraint checking |
| **L3** | Task boundaries and logical consistency | Boundary node evaluation |
| **L4** | Motor status, temperature, watchdogs | Hardware sink health queries |

Each guard is **completely independent**. You can enable/disable any layer in your Stackfile.

### 4. **Boundary System** (Configuration-driven)

Boundaries define the **safety envelope** active during a task. They are pure data — no code required.

```yaml
boundaries:
  pick_and_place:
    type: list
    nodes:
      - node_id: reach
        constraint:
          max_speed: 0.3
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0
      - node_id: grasp
        constraint:
          max_speed: 0.08
```

---

## Adapters & Integrations

DAM plugs into your hardware and policy via **duck-typed adapters**. If it quacks like a source/policy/sink, DAM will work with it.

### Sources (Observations)
```python
class MySource:
    def read(self) -> Observation:
        """Return current sensor state."""
        return Observation(...)
```

### Policies (Action Proposals)
```python
class MyPolicy:
    def step(self, obs: Observation) -> Action:
        """Propose an action given the observation."""
        return Action(...)
```

### Sinks (Hardware)
```python
class MySink:
    def write(self, action: Action) -> None:
        """Execute the action on hardware."""
        ...

    def health_check(self) -> HealthStatus:
        """Report hardware health for L4 guard."""
        return HealthStatus(...)
```

**Built-in adapters:**
- **LeRobot** (SO-ARM101 / Koch v1.1)
- **ROS 2** (joint states, trajectories, TF)
- **Simulation** (MuJoCo, PyBullet, etc.)

---

## Stackfile: The Configuration Format

A **Stackfile** is a YAML file that wires together all components. No Python code required for tier-1 deployments.

```yaml
dam:
  version: "1"

hardware:
  preset: so101_follower
  sources:
    follower_arm:
      type: lerobot
      port: /dev/tty.usbmodem5AA90244141
  sinks:
    follower_command:
      ref: sources.follower_arm

policy:
  type: lerobot
  model_id: lerobot/aloha-2-mobile-aloha/2024-07-29

guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, ...]
      lower_limits: [-1.57, -1.57, -1.57, ...]
      max_velocity: [1.5, 1.5, 1.5, ...]

boundaries:
  always_active: default
  containers:
    default:
      type: single
      nodes:
        - node_id: default
          constraint:
            max_speed: 0.3

tasks:
  pick_and_place:
    boundaries: [default]
```

---

## Runtime Modes

### **Managed Mode**
DAM runs its own control loop at a fixed frequency.

```python
runtime = GuardRuntime.from_stackfile("mystack.yaml")
runtime.start_task("mytask")
runtime.run()  # Blocks; runs at ~50 Hz until KeyboardInterrupt
```

### **Passive Mode** (Default)
Your code drives the loop. DAM executes one cycle per `step()` call.

```python
runtime = GuardRuntime.from_stackfile("mystack.yaml")
runtime.start_task("mytask")

for _ in range(1000):
    result = runtime.step()
    print(f"Risk: {result.risk_level}, Clamped: {result.was_clamped}")
```

---

## Telemetry & Observability

### Ring Buffer
Every cycle writes a `CycleResult` to an in-memory ring buffer. Latest N events are queryable via REST API.

### MCAP Loopback
When a guard rejects or clamps, DAM captures:
- ±30 seconds of observations
- All intermediate guard decisions
- Risk level timeline

Data is written in [MCAP](https://mcap.dev/) format — a standardized record format for robotics.

### Services API
Real-time REST + WebSocket endpoints:
- `GET /api/telemetry/history` — last N cycles
- `WS /ws/telemetry` — live stream
- `GET /api/risk-log` — historical queries
- `GET /api/risk-log/export/json` — data export

---

## Hot-Reload

Modify your Stackfile on disk → DAM reloads within ~500ms, no control loop interruption.

```python
from dam.config.hot_reload import StackfileWatcher

watcher = StackfileWatcher(
    path="mystack.yaml",
    on_change=runtime.apply_pending_reload,
    poll_interval_s=0.5,
)
watcher.start()
# Edit mystack.yaml on disk → changes take effect mid-run
```

Only **static** configuration (guard limits, boundary constraints) reloads. Guard class structure and task definitions remain fixed during a run.

---

## Design Principles

1. **Fail-to-Reject** — any guard timeout, exception, or unexpected behavior results in immediate rejection
2. **Defense-in-Depth** — safety is not a single check; it's five independent layers
3. **Configuration over Code** — use YAML for 99% of deployments; Python for advanced tier-2/3 setups
4. **Modularity** — swap hardware, policies, and safety rules independently
5. **Observability** — every decision is auditable; violations are captured for post-incident analysis
6. **Deterministic Execution** — Rust data plane eliminates GIL contention and garbage collection pauses

---

## Next Steps

- **Understand the guards** → [Guard Stack Explained](guards-explained.md)
- **Learn about safety** → [Safety Guarantees](safety.md)
- **Configure boundaries** → [Boundary System](boundaries.md)
- **Deploy** → [Quick Start Guide](../quick-stack.md)
