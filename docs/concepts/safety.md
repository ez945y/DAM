# Safety Model

DAM is designed using **defense-in-depth** and **fail-to-reject** principles. This document explains the safety behavior DAM is designed to provide, the assumptions it depends on, and what it does not guarantee.

---

## Core Safety Principle: Fail-to-Reject

**The most important rule: guard failures should result in rejection, not silent execution.**

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

The intended behavior is conservative: do not execute an action when the guard stack cannot evaluate it reliably.

---

## Four-Layer Defense

Each guard layer is independent and evaluates the action from a different perspective. The **most restrictive decision wins**.

### Layer 0: Out-of-Distribution (OOD) Detection

**What it guards against:** Policy hallucinations on unfamiliar states.

The policy was trained on a distribution of observations (e.g., arm configurations near a table). If the robot enters an unfamiliar state (e.g., hanging from a cable), the policy output cannot be trusted.

**How it works:**
- **Memory Bank** (when trained) — during normal operation, DAM records reference observations. At evaluation time, OODGuard computes a feature vector and finds the nearest neighbor in the bank. High distance = out-of-distribution.
- **Welford Z-score** (fallback) — maintains a running mean and variance of all observations. Rejects if any dimension's z-score exceeds threshold.

**Does NOT guarantee:**
- Perfect OOD detection (like all statistical methods, it has false negatives and false positives)
- Policy safety even on in-distribution observations (that's L1–L3's job)

**Typical configuration:**
```yaml
guards:
  - L0: ood
    phase: 0

boundaries:
  ood_welford:
    layer: L0
    type: single
    nodes:
      - callback: ood_welford
        fallback: hold_position
        params:
          z_threshold: 3.0
```

---

### Layer 1: Motion Safety / Physical Kinematics (L1)

**What it guards against:** Joint violations, workspace violations, velocity/acceleration overruns.

L1 is the most mature layer. It enforces hard kinematic and dynamic constraints.

**Constraints:**
1. **Joint position limits** — clamps if joint exceeds `[lower_limit, upper_limit]`
2. **Velocity limits** — scales action if joint velocity would exceed `max_velocities`
3. **Acceleration limits** — scales action if implied acceleration would exceed `max_acceleration`
4. **Workspace bounds** — rejects if end-effector goes outside `[xmin..xmax, ymin..ymax, zmin..zmax]`

**Example:**
```yaml
guards:
  - L1: motion
    phase: 0

boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
  joint_velocity_limit:
    layer: L1
    type: single
    nodes:
      - callback: joint_velocity_limit
        params:
          max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
```

**Behavior:**
- **Joint position:** Clamped to limits
- **Velocity:** Proportionally scaled (all joints by same ratio)
- **Acceleration:** Target velocity scaled back
- **Workspace:** **Rejected** (cannot clamp an end-effector back into bounds without knowing which joints to move)

**Expected behavior when configured correctly:**
- Joint limits are clamped to configured `upper` and `lower` values.
- Velocity and acceleration are bounded according to configured limits.
- Workspace callbacks respond when end-effector pose leaves the configured box.
- Collision-free motion still requires a dedicated simulation or collision checker.

---

### Layer 2: Task Execution (L2)

**What it guards against:** Actions that violate task-level constraints.

Boundaries define the safety envelope for a task. L2 enforces them.

**Checks (in order):**
1. **Callback** — executes the active node's registered L2 boundary callback
2. **Timeout** — rejects if boundary node has been active > `timeout_sec`

Built-in L2 callbacks include task speed, task workspace, gripper clearance,
and task-phase gripper command validation.

**Example:**
```yaml
boundaries:
  pick_and_place:
    layer: L2
    type: list
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0
```

**Expected behavior when configured correctly:**
- Actions violating active boundary callbacks are rejected or clamped according to the callback.
- Timeouts reject task phases that exceed `timeout_sec`.
- Custom callback correctness remains the user's responsibility.

---

### Layer 3: Hardware Monitoring (L3)

**What it guards against:** Hardware faults (motor overheating, disconnection, watchdog timeout).

L3 queries the hardware sink to check motor status, temperature, and other health indicators.

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

L3 rejects any action if:
- Motor is faulted
- Temperature exceeds safe limit
- Watchdog is not responding
- Sensor is disconnected

**Expected behavior when configured correctly:**
- Actions are rejected when configured hardware health checks report unhealthy state.
- DAM cannot prevent hardware faults themselves; it can only react to signals it receives.

---

## Runtime Safety Considerations

### Rust Data Plane

The Rust layer is responsible for the real-time critical path:
- Observation bus multiplexing
- Action evaluation
- Decision caching

**Runtime properties:**
- Rust helps avoid memory-safety classes such as use-after-free and data races.
- Moving high-volume messaging away from Python reduces GIL-related timing noise.
- Actual cycle timing must still be measured on the target machine and watched in the console.

### Python Fallback

If Rust extension is not compiled:
- The same guard semantics are used where supported.
- The Python runtime may have more timing variability.
- Validate latency before using hardware.

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

**Expected behavior:**
- Partial or invalid new config is not applied.
- Guards see a consistent config snapshot.
- Validate hot-reload behavior in your own deployment before relying on it around hardware.

---

## What DAM Does NOT Guarantee

### 1. Policy Safety
DAM **intercepts and validates actions**, but it does not guarantee the policy itself is safe.

```python
# Policy: "always move to [10, 10, 10] meters"
# This is physically impossible, but policy doesn't know that.
# L1 guard will reject it.  ✓

# Policy: "move to [1, 1, 1], but only if you see a red object"
# If the policy hallucinates red, action is still proposed to DAM.
# OOD guard may catch it, but not guaranteed.  ⚠️
```

### 2. Collision Avoidance
DAM does **not** inherently prevent collisions. Add a dedicated simulation or
collision-checking boundary when collision guarantees are required:

Use **task boundaries** (L2) to constrain reachable workspace as a proxy for collision safety.

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
The architecture is designed for conservative behavior:
- Fail-to-reject principle
- Layered guards
- Hot-reload atomicity
- Memory safety (Rust)

### Implementation Safety
It still requires careful code review and testing:
- No logic bugs in guard evaluators
- No off-by-one errors in boundary checks
- Proper error handling

DAM includes:
- Unit, integration, safety, and regression tests
- MCAP replay for post-incident analysis
- No formal verification claim

---

## Practical Safety Recommendations

### 1. Layer Your Guards
Always enable multiple guards. Don't rely on a single layer.

```yaml
guards:
  - L0: ood
    phase: 0
  - L1: motion
    phase: 0
  - L2: execution
    phase: 1
  - L3: hardware
    always: true
```

### 2. Tight Boundaries
Start with conservative boundary parameters. Loosen them incrementally as you validate behavior.

```yaml
# Phase 1: Very conservative
boundaries:
  reach:
    layer: L2
    type: single
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.1, 0.1], [0.1, 0.2], [0.0, 0.3]]

# Phase 2: Loosen as you gain confidence
boundaries:
  reach:
    layer: L2
    type: single
    nodes:
      - callback: task_workspace_bounds
        params:
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
dam validate mystack.yaml
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
- **Prepare hardware carefully** → [Hardware Readiness](../getting-started/hardware-readiness.md)
- **Monitor with the Console** → [DAM Console](../console.md)

---

## Questions?

Safety is paramount. If you have concerns about a specific scenario:
1. Check [GitHub Discussions](https://github.com/ez945y/DAM/discussions)
2. File an issue with the safety tag
3. Contact the DAM team

**Remember:** DAM is currently experimental-grade. For safety-critical production use, combine it with formal methods, extensive testing, independent hardware safety procedures, and human oversight.
