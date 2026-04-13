# Safety Guarantees

DAM is designed using **defense-in-depth** and **fail-safe** principles. This document explains what safety properties DAM provides, what it does NOT guarantee, and how to reason about safety in your system.

---

## Core Safety Principle: Fail-to-Reject

**The most important rule: Any failure mode in the guard stack results in immediate rejection.**

```python
try:
    decision = guard.evaluate(action, observation, state)
except Exception:
    decision = REJECT  # Timeout, memory error, logic error → REJECT

if decision_time > timeout_budget_ms:
    decision = REJECT  # Guard took too long → REJECT
```

This means:
- ✅ Guard exceptions → action rejected, not executed
- ✅ Guard timeout → action rejected, not executed
- ✅ Guard memory error → action rejected, not executed
- ✅ Corrupt data → action rejected, not executed

There is **no graceful degradation** that still executes unsafe actions.

---

## Five-Layer Defense

Each guard layer is independent and evaluates the action from a different perspective. The **most restrictive decision wins**.

### Layer 0: Out-of-Distribution (OOD) Detection

**What it guards against:** Policy hallucinations on unfamiliar states.

The policy was trained on a distribution of observations (e.g., arm configurations near a table). If the robot enters an unfamiliar state (e.g., hanging from a cable), the policy output cannot be trusted.

**How it works:**
- **Memory Bank** (when trained) — during normal operation, DAM records reference observations. At evaluation time, OODGuard computes a feature vector and finds the nearest neighbor in the bank. High distance = out-of-distribution.
- **Welford Z-score** (fallback) — maintains a running mean and variance of all observations. Rejects if any dimension's z-score exceeds threshold.

**Does NOT guarantee:**
- Perfect OOD detection (like all statistical methods, it has false negatives and false positives)
- Policy safety even on in-distribution observations (that's L2–L4's job)

**Typical configuration:**
```yaml
guards:
  builtin:
    ood:
      enabled: true
      params:
        nn_threshold: 0.5        # max allowed NN distance
        reconstruction_threshold: 0.05
```

---

### Layer 1: Preflight Simulation (Experimental)

**What it guards against:** Actions that violate physics constraints.

Before executing a proposed action, DAM simulates it in a shadow physics model to predict whether it will succeed.

**How it works:**
- Copy current state to a simulator
- Apply proposed action
- Run simulator forward for 100–500ms
- Check if end-effector reaches target, collides, or violates constraints
- Reject if simulation predicts failure

**Does NOT guarantee:**
- Sim-to-real fidelity (friction, dynamics uncertainties still exist)
- Real-time execution on slow hardware (simulation takes time)

**Status:** Currently in development. When available, it provides strong guarantees for grasp and reach tasks.

---

### Layer 2: Motion Safety (L2)

**What it guards against:** Joint violations, workspace violations, velocity/acceleration overruns.

L2 is the most mature layer. It enforces hard kinematic and dynamic constraints.

**Constraints:**
1. **Joint position limits** — clamps if joint exceeds `[lower_limit, upper_limit]`
2. **Velocity limits** — scales action if joint velocity would exceed `max_velocity`
3. **Acceleration limits** — scales action if implied acceleration would exceed `max_acceleration`
4. **Workspace bounds** — rejects if end-effector goes outside `[xmin..xmax, ymin..ymax, zmin..zmax]`

**Example:**
```yaml
guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
      max_velocity: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
      max_acceleration: [3.0, 3.0, 3.0, 3.0, 3.0, 1.0]
      bounds: [[-0.5, 0.5], [-0.1, 0.6], [0.0, 1.5]]
```

**Behavior:**
- **Joint position:** Clamped to limits
- **Velocity:** Proportionally scaled (all joints by same ratio)
- **Acceleration:** Target velocity scaled back
- **Workspace:** **Rejected** (cannot clamp an end-effector back into bounds without knowing which joints to move)

**Guarantees:**
- ✅ Joint limits are **never** violated
- ✅ Velocity and acceleration are bounded
- ✅ End-effector stays within workspace
- ❌ Does NOT guarantee collision-free motion (use Preflight Sim for that)

---

### Layer 3: Task Execution (L3)

**What it guards against:** Actions that violate task-level constraints.

Boundaries define the safety envelope for a task. L3 enforces them.

**Checks (in order):**
1. **Max speed** — rejects if joint velocity norm exceeds limit
2. **Bounds** — rejects if end-effector leaves defined bounds
3. **Max force** — rejects if force/torque sensor reading exceeds limit
4. **Callbacks** — executes user-registered Python callbacks; rejects if any return `False`
5. **Timeout** — rejects if boundary node has been active > `timeout_sec`

**Example:**
```yaml
boundaries:
  pick_and_place:
    type: list
    nodes:
      - node_id: reach
        constraint:
          max_speed: 0.3
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
          callback: [validate_reach_target]
        fallback: hold_position
        timeout_sec: 15.0
```

**Guarantees:**
- ✅ Actions violating boundary constraints are rejected
- ✅ Timeouts prevent indefinite task execution
- ❌ Callbacks are user-provided; DAM cannot guarantee they are correct

---

### Layer 4: Hardware Monitoring (L4)

**What it guards against:** Hardware faults (motor overheating, disconnection, watchdog timeout).

L4 queries the hardware sink to check motor status, temperature, and other health indicators.

**Example health check:**
```python
class MySink:
    def health_check(self) -> HealthStatus:
        return HealthStatus(
            motors_ok=True,
            temp_celsius=45.2,  # Normal
            watchdog_ok=True,
            connected=True,
        )
```

L4 rejects any action if:
- Motor is faulted
- Temperature exceeds safe limit
- Watchdog is not responding
- Sensor is disconnected

**Guarantees:**
- ✅ Actions are rejected if hardware is unhealthy
- ❌ Cannot prevent hardware faults themselves (only detect them)

---

## Memory Safety & Real-Time Guarantees

### Rust Data Plane

The Rust layer is responsible for the real-time critical path:
- Observation bus multiplexing
- Action evaluation
- Decision caching

**Safety properties:**
- ✅ **No buffer overflows** — Rust's type system prevents out-of-bounds access
- ✅ **No use-after-free** — borrow checker ensures lifetime safety
- ✅ **No data races** — Send/Sync traits enforced at compile time
- ✅ **No garbage collection** — deterministic memory deallocation
- ✅ **No GIL contention** — guards can run in parallel without fighting Python's GIL

**Worst-case execution time (WCET):**
- Per-cycle guard evaluation: < 5 ms (typical hardware)
- Can be formally analyzed if needed

### Python Fallback

If Rust extension is not compiled:
- ✅ Still safe (Python implementation uses same logic)
- ⚠️ Slower (GIL may introduce unpredictable latency)
- ⚠️ WCET harder to analyze (Python runtime not deterministic)

---

## Hot-Reload Safety

When you edit a Stackfile and trigger a reload:

```python
watcher = StackfileWatcher(
    path="mystack.yaml",
    on_change=runtime.apply_pending_reload,
)
```

DAM performs atomic updates:
1. Parse new Stackfile
2. Validate against schema
3. **Verify all guards are in consistent state**
4. Swap config atomically at the start of the next cycle
5. Old config is kept as fallback if validation fails

**Guarantees:**
- ✅ Partial/invalid new config is **never** applied (all-or-nothing)
- ✅ Guards see consistent state (no torn reads)
- ✅ No control loop interruption

---

## What DAM Does NOT Guarantee

### 1. Policy Safety
DAM **intercepts and validates actions**, but it does not guarantee the policy itself is safe.

```python
# Policy: "always move to [10, 10, 10] meters"
# This is physically impossible, but policy doesn't know that.
# L2 guard will reject it.  ✓

# Policy: "move to [1, 1, 1], but only if you see a red object"
# If the policy hallucinates red, action is still proposed to DAM.
# OOD guard may catch it, but not guaranteed.  ⚠️
```

### 2. Collision Avoidance
DAM does **not** inherently prevent collisions. L1 (Preflight Sim) helps, but:
- Not perfect (sim-to-real gap)
- Not always enabled (tier-1 deployments may skip it)
- Requires accurate collision geometry

Use **task boundaries** (L3) to constrain reachable workspace as a proxy for collision safety.

### 3. Human Safety in Collaborative Tasks
DAM is **not certified** for human-robot collaboration. It cannot:
- Detect human presence reliably
- Predict human motion
- Comply with ISO/TS 15066 force/torque limits (though L3 can enforce them if you specify thresholds)

### 4. Protection Against Adversarial Inputs
DAM assumes your sensor data is honest. If an attacker spoofs sensor values:
- OOD guard may not catch it
- Policies may produce unsafe outputs
- DAM will reject based on the corrupted data

### 5. Formal Safety Proof
DAM's design follows best practices, but proofs are ongoing work. The system is **experimental-grade**, not formally verified.

---

## Design vs. Implementation

DAM has two safety components:

### Design Safety
The **architecture itself** is safe by construction:
- Fail-to-reject principle
- Layered guards
- Hot-reload atomicity
- Memory safety (Rust)

### Implementation Safety
Requires careful code review and testing:
- No logic bugs in guard evaluators
- No off-by-one errors in boundary checks
- Proper error handling

DAM includes:
- ✅ Comprehensive unit tests
- ✅ Integration tests with real hardware
- ✅ MCAP replay for post-incident analysis
- ⚠️ No formal verification (yet)

---

## Practical Safety Recommendations

### 1. Layer Your Guards
Always enable multiple guards. Don't rely on a single layer.

```yaml
guards:
  builtin:
    ood:
      enabled: true
    motion:
      enabled: true
    execution:
      enabled: true
    hardware:
      enabled: true
```

### 2. Tight Boundaries
Start with conservative constraints. Loosen them incrementally as you verify safety.

```yaml
# Phase 1: Very conservative
boundaries:
  reach:
    nodes:
      - constraint:
          max_speed: 0.1
          bounds: [[-0.1, 0.1], [0.1, 0.2], [0.0, 0.3]]

# Phase 2: Loosen as you gain confidence
boundaries:
  reach:
    nodes:
      - constraint:
          max_speed: 0.3
          bounds: [[-0.3, 0.3], [0.05, 0.45], [0.01, 0.40]]
```

### 3. Monitor the MCAP Buffer
When a reject or clamp occurs, analyze the ±30-second context:

```bash
# Export violations for offline analysis
curl http://localhost:8080/api/risk-log/export/json > violations.json
mcap cat violations.mcap | jq '.' | head -100
```

### 4. Test Fallbacks
Before deploying to hardware, verify fallback behavior:

```python
runtime.inject_rejection_for_testing()  # Force next N cycles to test fallbacks
```

### 5. Use Stackfile Validation
Always validate your Stackfile before loading:

```bash
dam validate --stack mystack.yaml
```

---

## Versioning & Updates

DAM is versioned according to the Stackfile schema. Breaking changes in guard behavior are rare, but:
- Guard parameters may be added/removed
- New guard layers may be introduced
- Fallback strategies may expand

Always test new versions in simulation before deploying to hardware.

---

## Next Steps

- **Understand the guards in detail** → [Guard Stack Explained](guards-explained.md)
- **Configure boundaries** → [Boundary System](boundaries.md)
- **Deploy your system** → [Quick Start Guide](../quick-stack.md)
- **Monitor with the Console** → [DAM Console](../console.md)

---

## Questions?

Safety is paramount. If you have concerns about a specific scenario:
1. Check [GitHub Discussions](https://github.com/ez945y/DAM/discussions)
2. File an issue with the safety tag
3. Contact the DAM team

**Remember:** DAM is currently experimental-grade. For safety-critical production use, combine it with formal methods, extensive testing, and human oversight.
