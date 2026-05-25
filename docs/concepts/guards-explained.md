# Guard Stack Explained

The **guard stack** is the heart of DAM. Four independent layers evaluate every proposed action from different angles. This document explains how each guard works, what they detect, and how to configure them.

---

## First-Read Path

If you are learning DAM for the first time, read in this order:

1. Guard Stack Flow
2. Layer 1 Motion Safety
3. Layer 2 Task Execution
4. Layer 3 Hardware Monitoring
5. Layer 0 OOD Detection

Use this page after you can already validate a Stackfile and identify pass, clamp, reject, and fallback decisions.

---

## The Guard Stack Flow

```
Observation
    ↓
[ L0 — OOD Detection ]       ← Is this state familiar?
    ↓ (if passes)
[ L1 — Physical Kinematics ] ← Are joint limits and motion constraints safe?
    ↓ (if passes)
[ L2 — Task Execution ]      ← Does this command fit the current task phase?
    ↓ (if passes)
[ L3 — Hardware Monitor ]    ← Is the hardware healthy?
    ↓
DECISION: Pass / Clamp / Reject

If rejected → Fallback Engine → Hold / Retreat / E-Stop
```

The **most restrictive decision wins**. If any layer says REJECT, the action is rejected. If multiple layers clamp, they are applied in order.

---

## Layer 0: OOD Detection (Out-of-Distribution)

**Responsibility:** Detect when the robot enters an unfamiliar state.

### The Problem

ML policies are trained on a distribution of data. When the robot encounters a state *outside* that distribution, the policy's output is unreliable.

**Example:**
- Policy trained on arm configurations near a table (0.0–0.5m height)
- Robot moves to 2.0m (unfamiliar state)
- Policy still produces an output, but it's a hallucination

OOD Detection catches this and rejects the action.

### How It Works

**Method 1: Memory Bank (when trained)**

```python
from dam.guard.builtin.ood import OODGuard

# Training phase
guard = OODGuard()
guard.train(reference_observations)  # List of Observation objects
guard.save("extractor.pt", "bank.npy")
```

At inference:
1. Extract a feature vector from the observation (128-dim L2-normalized)
2. Find the nearest neighbor in the trained memory bank
3. If distance > threshold → reject

**Method 2: Welford Z-Score (fallback)**

If no memory bank is trained:
1. Maintain running mean and variance of all observations
2. For each dimension, compute z-score: `(x - mean) / std`
3. If `max_z > threshold` → reject
4. Requires 30-cycle warm-up period

### Configuration

```yaml
guards:
  - L0: ood
    phase: 0

boundaries:
  ood_detector:
    layer: L0
    type: single
    nodes:
      - callback: ood_detector
        fallback: hold_position
        params:
          backend: welford
          z_threshold: 3.0
```

### When to Use

- ✅ Sim-to-real transfer (detects when real world looks different from simulation)
- ✅ Multi-environment deployment (detects when you move to a new room)
- ✅ Graceful degradation (rejects instead of guessing on unfamiliar states)
- ❌ NOT a substitute for good training data (train on diverse data first)

### Behavior & Limitations

| Aspect | Status |
|--------|--------|
| Catches distribution shift | ✅ Usually |
| False positives | ⚠️ Possible (rejects valid states) |
| False negatives | ⚠️ Possible (misses subtle shifts) |
| Timing impact | Watch cycle latency on the target machine |
| User configuration required | ✅ Training phase optional but recommended |

---

## Layer 1: Motion Safety (L1)

**Responsibility:** Enforce joint limits, velocity bounds, and workspace constraints.

**Status:** Implemented and ready for research and supervised development use.

This is the most important and mature layer. It prevents kinematic and dynamic violations.

### Four Types of Constraints

#### 1. Joint Position Limits
```yaml
boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
```

**Behavior:** Clamp proposed joint position to `[lower, upper]`.

#### 2. Velocity Limits
```yaml
boundaries:
  joint_velocity_limit:
    layer: L1
    type: single
    nodes:
      - callback: joint_velocity_limit
        params:
          max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
```

**Behavior:** If any joint velocity exceeds limit, scale **all** velocities by the same ratio.

```python
# Example
proposed_velocities = [2.0, 0.5, 0.5]  # rad/s
max_velocities = [1.0, 1.0, 1.0]

# Joint 0 violates: 2.0 > 1.0
# Scale factor: 1.0 / 2.0 = 0.5
executed_velocities = [1.0, 0.25, 0.25]  # All scaled by 0.5
```

#### 3. Acceleration Limits
```yaml
boundaries:
  joint_acceleration_limit:
    layer: L1
    type: single
    nodes:
      - callback: joint_acceleration_limit
        fallback: hold_position
        params:
          max_accelerations: [3.0, 3.0, 3.0, 3.0, 3.0, 1.0]
```

**Behavior:** If implied acceleration would exceed limit, scale target velocity down.

```python
# Example
current_velocity = [0.5, 0.5, 0.5]
proposed_velocity = [2.0, 2.0, 2.0]  # 50 Hz, dt = 0.02s
implied_accel = (2.0 - 0.5) / 0.02 = 75 rad/s²
max_accel = [3.0, ...]

# Too fast! Reduce target velocity
target_velocity = current_velocity + (max_accel * dt)
                = [0.5, ...] + [3.0 * 0.02, ...]
                = [0.56, ...]
```

#### 4. Workspace Bounds
```yaml
boundaries:
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.5, 0.5], [-0.1, 0.6], [0.0, 1.5]]
```

**Behavior:** Compute end-effector position. If outside bounds → **REJECT** (cannot clamp without knowing which joints to move).

### Configuration Template

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
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.5, 0.5], [-0.1, 0.6], [0.0, 1.5]]
```

### Decision Table

| Constraint | Action | Decision |
|-----------|--------|----------|
| Position exceeds limit | Clamp to limit | PASS (clamped) |
| Velocity exceeds max | Scale proportionally | PASS (clamped) |
| Acceleration exceeds max | Reduce target velocity | PASS (clamped) |
| End-effector outside workspace | Cannot fix | **REJECT** |

### Expected Enforcement

| Aspect | Status |
|--------|--------|
| Joint limits | Enforced when configured with accurate limits |
| Velocity bounds | Enforced when configured with accurate limits |
| Acceleration bounds | Enforced when configured with accurate limits |
| Workspace bounds | Enforced when configured with a valid kinematic model |
| Collision-free | Not covered by the default guard stack; requires a dedicated simulation or collision checker |
| Timing health | Watch cycle latency in the console and configure watchdogs for the target hardware |

---

## Layer 2: Task Execution (L2)

**Responsibility:** Enforce task-specific boundaries and constraints.

Boundaries define the **safety envelope** for a task phase. L2 checks if the proposed action respects the active boundary.

### Example Boundary

```yaml
boundaries:
  pick_and_place:
    layer: L2
    type: list
    nodes:
      - callback: task_gripper_command_guard
        params:
          allowed_command: close
          zone: [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
        fallback: hold_position
      - callback: task_gripper_command_guard
        params:
          allowed_command: none
        fallback: hold_position
      - callback: task_gripper_command_guard
        params:
          allowed_command: open
          zone: [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]
        fallback: hold_position
```

### Common L2 Callbacks

| Callback | Behavior |
|----------|----------|
| `task_joint_speed_limit` | Reject if joint velocity norm exceeds task limit |
| `task_workspace_bounds` | Reject if end-effector leaves task workspace |
| `check_gripper_clear` | Reject if gripper is closed when it must be clear |
| `task_gripper_command_guard` | Clamp open/close commands that do not match the active task node or its zone by suppressing the gripper command |

### Evaluation Order

1. **callback** — active node's registered L2 boundary callback
2. **timeout_sec** — node active time check

If any check fails, evaluation stops and decision is REJECT.

### Custom Callbacks

Use built-in callbacks first. Add a custom callback only when the task needs a
check that is not already covered by the callback catalog. See
[Boundary Callbacks](../boundary-callbacks.md) for the current registration
pattern and available built-ins.

### Expected Behavior

| Aspect | Status |
|--------|--------|
| Boundary callbacks run | When the task activates the boundary and the Stackfile validates |
| Timeouts prevent indefinite phases | When `timeout_sec` is configured on the node |
| Task gripper command compatibility | `task_gripper_command_guard` can suppress incompatible gripper commands |
| Callback correctness | User's responsibility for custom callbacks |

---

## Layer 3: Hardware Monitoring (L3)

**Responsibility:** Check hardware health and reject if faulted.

### Health Status

L3 queries the hardware sink for health information:

```python
@dataclass
class HealthStatus:
    motors_ok: bool               # Motor fault flags
    temp_celsius: float           # Motor temperature
    watchdog_ok: bool             # Watchdog responding
    connected: bool               # Hardware connected
    error_message: Optional[str]  # Last error
```

### Example Sink Implementation

```python
class MySink:
    def health_check(self) -> HealthStatus:
        motor_temps = self.read_motor_temps()  # Query hardware
        return HealthStatus(
            motors_ok=all(not m.faulted for m in self.motors),
            temp_celsius=max(motor_temps),
            watchdog_ok=self.watchdog.is_alive(),
            connected=self.is_connected(),
            error_message=self.last_error,
        )
```

### Configuration

```yaml
guards:
  - L3: hardware
    always: true

boundaries:
  hardware_watchdog:
    layer: L3
    type: single
    nodes:
      - callback: hardware_watchdog
        timeout_sec: 0.5
        fallback: emergency_stop
        params:
          max_temperature_c: 60.0
          max_staleness_ms: 1000
```

### Expected Behavior

| Aspect | Status |
|--------|--------|
| Hardware faults detected | When the adapter exposes health status |
| Temperature monitored | When temperature sources are configured |
| Watchdog enforced | When watchdog boundaries are active |
| Hardware damage prevention | Depends on sensor accuracy, thresholds, and independent stop procedures |

---

## Guard Layering Strategy

### Conservative Approach (Recommended for Learning)

Enable **all** guards. Let them work together.

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

**Benefit:** Multiple layers catch different failure modes.
**Trade-off:** More restrictive (may reject safe actions).

### Aggressive Approach (After Validation)

Disable less relevant guards to speed up execution.

```yaml
guards:
  - L0: ood
    enabled: false              # Skip if OOD is rare
  - L1: motion
    phase: 0
  - L2: execution
    phase: 1
  - L3: hardware
    always: true
```

**Benefit:** Faster control loop.
**Risk:** Single-layer failures are not caught.

---

## Performance Characteristics

| Guard | Typical Latency | Complexity | GPU Required |
|-------|-----------------|-----------|--------------|
| L0 OOD | 0.1–1.0 ms | High (NN lookup) | Optional (FeatureExtractor) |
| L1 Motion | < 1 ms | Low (algebraic checks) | No |
| L2 Task Execution | < 1 ms | Low–medium (task callbacks) | No |
| L3 Hardware | < 0.5 ms | Low (sensor queries) | No |

**Total typical per-cycle latency:** 1–5 ms at 50 Hz control frequency.

---

## Common Configurations

### Simulation (Fast, No Hardware)

```yaml
guards:
  - L0: ood
    phase: 0
  - L1: motion
    phase: 0
  - L2: execution
    phase: 1
  - L3: hardware
    enabled: false              # No hardware to monitor
```

### Hardware Deployment (Conservative)

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
          max_velocities: [1.0, 1.0, 1.0, 1.0, 1.0, 0.3]
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.3, 0.3], [0.1, 0.5], [0.0, 1.0]]
  hardware_watchdog:
    layer: L3
    type: single
    nodes:
      - callback: hardware_watchdog
        timeout_sec: 0.5
        fallback: emergency_stop
        params:
          max_staleness_ms: 1000
          monitor_temperature: true
          max_temperature_c: 80
```

### Multi-Robot Deployment (Heterogeneous)

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

---

## Next Steps

- **Configure boundaries** → [Boundary System](boundaries.md)
- **Learn about the safety model** → [Safety Model](safety.md)
- **Deploy with Stackfiles** → [Quick Start Guide](../quick-stack.md)
- **Dive deeper** → [Full Specification](../DAM_Specification.md)
