# DAM — Detachable Action Monitor

**Modular safety middleware for ML-driven robot control**

DAM is a real-time safety framework that sits between any machine learning policy and robot hardware. It intercepts every proposed action, evaluates it through a layered guard stack, and either **passes**, **clamps**, or **rejects** it—without modifying your policy weights or hardware drivers.

---

## Why DAM?

### The Problem

Deploying learned policies on real robots requires more than good training data. You need:
- **Hardware safety** — joint limits, velocity bounds, workspace constraints
- **Semantic understanding** — is this observation in the training distribution?
- **Task-aware logic** — does this action make sense for the current goal?
- **Hardware health** — are motors safe? Is temperature normal?

Traditional approaches either bake safety into the policy (rigid, hard to update) or throw everything at a single catch-all check (slow, opaque).

### The Solution

DAM decouples safety from learning. Safety becomes a **modular, swappable stack** you can:
- ✅ Modify safety rules without retraining the policy
- ✅ Enable/disable guards independently per task
- ✅ Hot-reload boundaries while the robot is running
- ✅ Audit and replay every decision
- ✅ Swap hardware drivers or policies without recompiling

---

## Core Strengths

| Feature | Benefit |
|---------|---------|
| **5-Layer Guard Stack** | Progressive defense from perception (L0) → hardware (L4) |
| **Rust Data Plane** | Deterministic, real-time-safe execution outside the Python GIL |
| **Stackfile-Driven** | Swap hardware, policies, and safety rules via simple YAML. Zero Python code for tier-1 deployments. |
| **Hot-Reload Boundaries** | Update safety constraints without stopping the loop |
| **Fail-to-Reject** | Guard timeouts, crashes, or exceptions → immediate rejection. Safe by default. |
| **Full Observability** | MCAP buffer captures ±30s of sensor context around every safety event |
| **Built-in Adapters** | LeRobot (SO-ARM101) and ROS 2 support out of the box |

---

## Quick Navigation

<div class="grid cards" markdown>

- **New to DAM?**
  Start with [Getting Started →](installation.md)

- **Deploy Your System**
  Read [Stackfile Guide →](quick-stack.md)

- **Monitor & Control**
  Use the [DAM Console →](console.md)

- **Integrate via API**
  Check [Services API →](services-api.md)

- **Deep Dive**
  See [Full Specification →](DAM_Specification.md)

</div>

---

## The Guard Stack

DAM evaluates actions through **5 independent layers**, each with a specific responsibility:

```
Policy Output (proposed action)
    ↓
[ L0 — OOD Detection ]       ← Is this observation familiar?
    ↓
[ L1 — Preflight Sim ]       ← Will this action work physically?
    ↓
[ L2 — Motion Safety ]       ← Are joint limits and dynamics safe?
    ↓
[ L3 — Task Execution ]      ← Does this fit the task boundaries?
    ↓
[ L4 — Hardware Monitor ]    ← Is the hardware healthy?
    ↓
DECISION: Pass / Clamp / Reject
    ↓
Hardware Command (or Fallback)
```

Each layer votes independently. The **most restrictive** decision wins. Layers can be enabled/disabled via Stackfile.

---

## Typical Use Cases

### 🦾 Collaborative Manipulation
Control a dual-arm robot with learned pick-and-place policies while enforcing workspace bounds, force limits, and emergency stops.

```yaml
guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, ...]
      max_force_n: 50.0
    execution:
      enabled: true
```

### 🤖 Mobile Manipulation
Deploy a Diffusion Policy on a mobile base. Keep the base within a geofence while the arm executes learned manipulation.

### 🔬 Research & Development
Rapidly prototype new policies without waiting for safety certification. DAM handles compliance; you focus on learning.

### 🏭 Sim-to-Real Transfer
Test policies in simulation, deploy directly to hardware with DAM guardrails. Update boundaries as you learn what works.

---

## How It Works (30-Second Version)

1. **Register adapters** — plug in your hardware (LeRobot, ROS 2, custom) and policy
2. **Write a Stackfile** — define guards, boundaries, and fallback strategies in YAML
3. **Start the runtime** — `dam run --stack mystack.yaml --task mytask`
4. **DAM steps every cycle:**
   - Read observations from hardware
   - Propose action from policy
   - Evaluate through 5-layer guard stack
   - Clamp/reject if unsafe
   - Send command to hardware
   - Log everything to MCAP

5. **Monitor in real-time** — open the DAM Console to watch guard decisions, latencies, and risk levels

---

## Installation & Quickstart

```bash
git clone https://github.com/ez945y/DAM.git && cd DAM
make setup   # one-time: venv + Rust extension + npm deps
make run     # start backend + console (http://localhost:3000)
```

`make setup` handles everything automatically — Python environment (via `uv`), Rust extension build (via `maturin`), and frontend dependencies (via `npm`). See [Installation →](installation.md) for prerequisites and hardware-specific setup.

---

## Safety First

DAM is built on **defense-in-depth** and **fail-safe** principles:

- **Fail-to-Reject** — any timeout, exception, or unexpected behavior in the guard stack results in immediate rejection
- **Memory Safety** — Rust data plane eliminates memory vulnerabilities
- **Deterministic Execution** — real-time-friendly, no GIL contention
- **Layered Verification** — safety is not a single point of failure

**Important:** DAM is currently **research and experimental-grade software**. It is not certified for safety-critical or production use in human-collaborative or high-risk environments. Use at your own risk. We are actively working toward formal verification, worst-case timing analysis, and compliance-oriented documentation.

---

## Learn More

| Topic | Where |
|-------|-------|
| **Deploy with Stackfiles** | [Stackfile Guide →](quick-stack.md) |
| **Monitor your system** | [Console Guide →](console.md) |
| **Control via API** | [Services API →](services-api.md) |
| **Guards reference** | [Guards Reference →](guards-reference.md) |
| **Boundary callbacks** | [Boundary Callbacks →](boundary-callbacks.md) |
| **Contribute** | [Contributing →](contributing.md) |

---

## Community & Support

- 📖 [Full Specification](DAM_Specification.md)
- 💬 [GitHub Discussions](https://github.com/ez945y/DAM/discussions)
- 🐛 [Report Issues](https://github.com/ez945y/DAM/issues)
- ✅ [Contribution Guidelines](contributing.md)

---

## Next Steps

Based on your role, here's where to start:

- **I want to get running fast** → [Installation →](installation.md)
- **I want to deploy a stack** → [Stackfile Guide →](quick-stack.md)
- **I want to monitor in real-time** → [Console Guide →](console.md)
- **I'm deploying to hardware** → [Installation — Hardware Support →](installation.md#hardware-support-so-arm101-lerobot)

---

**DAM makes advanced robot safety modular, verifiable, and accessible to the embodied AI community.**

*Built for safer embodied AI.*
