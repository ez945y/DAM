# Guard Stack Explained

The **guard stack** is the heart of DAM. Five independent layers evaluate every proposed action from different angles. This document explains how each guard works, what they detect, and how to configure them.

---

## The Guard Stack Flow

```
Observation
    ↓
[ L0 — OOD Detection ]       ← Is this state familiar?
    ↓ (if passes)
[ L1 — Preflight Sim ]       ← Will this action work physically?
    ↓ (if passes)
[ L2 — Motion Safety ]       ← Are joint limits and dynamics safe?
    ↓ (if passes)
[ L3 — Task Execution ]      ← Does this fit the task boundary?
    ↓ (if passes)
[ L4 — Hardware Monitor ]    ← Is the hardware healthy?
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
  builtin:
    ood:
      enabled: true
      params:
        nn_threshold: 0.5              # Memory bank distance threshold
        z_threshold: 3.0               # Z-score threshold for Welford
        ood_model_path: models/extractor.pt
        bank_path: models/bank.npy
```

### When to Use

- ✅ Sim-to-real transfer (detects when real world looks different from simulation)
- ✅ Multi-environment deployment (detects when you move to a new room)
- ✅ Graceful degradation (rejects instead of guessing on unfamiliar states)
- ❌ NOT a substitute for good training data (train on diverse data first)

### Guarantees & Limitations

| Aspect | Status |
|--------|--------|
| Catches distribution shift | ✅ Usually |
| False positives | ⚠️ Possible (rejects valid states) |
| False negatives | ⚠️ Possible (misses subtle shifts) |
| Real-time safe | ✅ Yes (< 1 ms) |
| User configuration required | ✅ Training phase optional but recommended |

---

## Layer 1: Preflight Simulation (L1)

**Responsibility:** Simulate action before execution to verify it will work.

**Status:** In development (Phase 2). Currently experimental.

### The Idea

Before commanding hardware, DAM can run a quick physics simulation:
1. Copy current state to simulator
2. Apply proposed action
3. Simulate forward 100–500ms
4. Verify end-effector reaches target, doesn't collide, etc.
5. Reject if simulation predicts failure

### Example Use Case

```yaml
# Policy proposes: "move to [0.3, 0.2, 0.1]"
# Simulator says: "Can't reach there (joint limit)"
# L1 rejects before sending to hardware
```

### Configuration (when available)

```yaml
guards:
  builtin:
    preflight_sim:
      enabled: true
      simulator: mujoco              # or pybullet
      forward_time_ms: 250
      collision_check: true
      joint_limit_check: true
```

### Guarantees & Limitations

| Aspect | Status |
|--------|--------|
| Prevents impossible actions | ✅ Yes |
| Sim-to-real fidelity | ⚠️ Good but imperfect (friction, damping) |
| Real-time safe | ⚠️ Depends on sim complexity |
| Requires sim model | ✅ Yes (URDF or MuJoCo XML) |

---

## Layer 2: Motion Safety (L2)

**Responsibility:** Enforce joint limits, velocity bounds, and workspace constraints.

**Status:** ✅ Fully implemented and production-ready.

This is the most important and mature layer. It prevents kinematic and dynamic violations.

### Four Types of Constraints

#### 1. Joint Position Limits
```yaml
guards:
  builtin:
    motion:
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
```

**Behavior:** Clamp proposed joint position to `[lower, upper]`.

#### 2. Velocity Limits
```yaml
guards:
  builtin:
    motion:
      max_velocity: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
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
guards:
  builtin:
    motion:
      max_acceleration: [3.0, 3.0, 3.0, 3.0, 3.0, 1.0]
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
guards:
  builtin:
    motion:
      bounds: [
        [-0.5, 0.5],     # x: -0.5 to 0.5 meters
        [-0.1, 0.6],     # y: -0.1 to 0.6 meters
        [0.0, 1.5]       # z: 0.0 to 1.5 meters
      ]
```

**Behavior:** Compute end-effector position. If outside bounds → **REJECT** (cannot clamp without knowing which joints to move).

### Configuration Template

```yaml
guards:
  builtin:
    motion:
      enabled: true

      # Joint limits (required)
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]

      # Velocity limits (optional)
      max_velocity: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]

      # Acceleration limits (optional)
      max_acceleration: [3.0, 3.0, 3.0, 3.0, 3.0, 1.0]

      # Workspace bounds (optional)
      bounds: [[-0.5, 0.5], [-0.1, 0.6], [0.0, 1.5]]

      # Phase 2 features
      params:
        velocity_scale: 1.0  # Runtime scale factor
```

### Decision Table

| Constraint | Action | Decision |
|-----------|--------|----------|
| Position exceeds limit | Clamp to limit | PASS (clamped) |
| Velocity exceeds max | Scale proportionally | PASS (clamped) |
| Acceleration exceeds max | Reduce target velocity | PASS (clamped) |
| End-effector outside workspace | Cannot fix | **REJECT** |

### Guarantees

| Aspect | Status |
|--------|--------|
| Joint limits never exceeded | ✅ Guaranteed |
| Velocity bounded | ✅ Guaranteed |
| Acceleration bounded | ✅ Guaranteed |
| Workspace enforced | ✅ Guaranteed |
| Collision-free | ❌ NO (use L1 preflight sim) |
| Real-time safe | ✅ Yes (< 1 ms) |

---

## Layer 3: Task Execution (L3)

**Responsibility:** Enforce task-specific boundaries and constraints.

Boundaries define the **safety envelope** for a task phase. L3 checks if the proposed action respects the active boundary.

### Example Boundary

```yaml
boundaries:
  pick_and_place:
    type: list
    nodes:
      - node_id: reach
        constraint:
          max_speed: 0.3
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
          max_force_n: null
          callback: [validate_reach_target]
        fallback: hold_position
        timeout_sec: 15.0
```

### Constraint Types

| Constraint | Type | Behavior |
|-----------|------|----------|
| `max_speed` | float | Reject if joint velocity norm > limit |
| `bounds` | `[[x_min, x_max], [y_min, y_max], [z_min, z_max]]` | Reject if end-effector outside bounds |
| `max_force_n` | float | Reject if force/torque norm > limit (requires sensor) |
| `callback` | list[string] | Reject if any registered callback returns `False` |
| `upper_limits` | list[float] | Reject if joint exceeds limit |
| `lower_limits` | list[float] | Reject if joint below limit |
| `max_velocity` | list[float] | Reject if per-joint velocity exceeds |

### Evaluation Order

1. **max_speed** — velocity norm check
2. **bounds** — end-effector position check
3. **max_force_n** — force/torque sensor check (if available)
4. **callback** — user-provided Python functions
5. **timeout_sec** — node active time check

If any check fails, evaluation stops and decision is REJECT.

### Callbacks

```python
# Register a callback
@dam.callback
def validate_reach_target(obs, state, constraint):
    """Return True to pass, False to reject."""
    target_distance = np.linalg.norm(
        obs.end_effector_pos - state.reach_target
    )
    return target_distance < 0.05  # Reject if too far

# Use in Stackfile
boundaries:
  reach:
    nodes:
      - node_id: reach
        constraint:
          callback: [validate_reach_target]
```

### Guarantees

| Aspect | Status |
|--------|--------|
| Boundary constraints enforced | ✅ Yes |
| Timeouts prevent indefinite phases | ✅ Yes |
| Force limits enforced | ✅ Yes (if sensor available) |
| Callback correctness | ⚠️ User's responsibility |

---

## Layer 4: Hardware Monitoring (L4)

**Responsibility:** Check hardware health and reject if faulted.

### Health Status

L4 queries the hardware sink for health information:

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
  builtin:
    hardware:
      enabled: true
      params:
        max_temp_celsius: 60.0
        require_watchdog: true
        require_connected: true
```

### Guarantees

| Aspect | Status |
|--------|--------|
| Hardware faults detected | ✅ Yes |
| Temperature monitored | ✅ Yes |
| Watchdog enforced | ✅ Yes |
| Prevents hardware damage | ✅ Usually (depends on sensor accuracy) |

---

## Guard Layering Strategy

### Conservative Approach (Recommended for Learning)

Enable **all** guards. Let them work together.

```yaml
guards:
  builtin:
    ood:
      enabled: true
    motion:
      enabled: true
      upper_limits: [...]
      max_velocity: [...]
    execution:
      enabled: true
    hardware:
      enabled: true
```

**Benefit:** Multiple layers catch different failure modes.
**Trade-off:** More restrictive (may reject safe actions).

### Aggressive Approach (After Validation)

Disable less relevant guards to speed up execution.

```yaml
guards:
  builtin:
    ood:
      enabled: false            # Skip if OOD is rare
    motion:
      enabled: true
      # Loosen limits as you gain confidence
      max_velocity: [2.0, 2.0, 2.0, ...]
    execution:
      enabled: true
    hardware:
      enabled: true
```

**Benefit:** Faster control loop.
**Risk:** Single-layer failures are not caught.

---

## Performance Characteristics

| Guard | Typical Latency | Complexity | GPU Required |
|-------|-----------------|-----------|--------------|
| L0 OOD | 0.1–1.0 ms | High (NN lookup) | Optional (FeatureExtractor) |
| L1 Preflight | 5–50 ms | Very high (simulation) | Optional |
| L2 Motion | < 1 ms | Low (algebraic checks) | No |
| L3 Execution | 0.5–2.0 ms | Medium (constraint evaluation) | No |
| L4 Hardware | < 0.5 ms | Low (sensor queries) | No |

**Total typical per-cycle latency:** 1–5 ms at 50 Hz control frequency.

---

## Common Configurations

### Simulation (Fast, No Hardware)

```yaml
guards:
  builtin:
    ood:
      enabled: true
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
      max_velocity: [2.0, 2.0, 2.0, 2.0, 2.0, 1.0]
    execution:
      enabled: true
    hardware:
      enabled: false  # No hardware to monitor
```

### Hardware Deployment (Conservative)

```yaml
guards:
  builtin:
    ood:
      enabled: true
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
      max_velocity: [1.0, 1.0, 1.0, 1.0, 1.0, 0.3]
      bounds: [[-0.3, 0.3], [0.1, 0.5], [0.0, 1.0]]
    execution:
      enabled: true
    hardware:
      enabled: true  # Monitor motor temp, watchdog, etc.
```

### Multi-Robot Deployment (Heterogeneous)

```yaml
guards:
  builtin:
    ood:
      enabled: true
      params:
        ood_model_path: models/policy_distribution.pt
    motion:
      enabled: true
      # Looser limits for experienced robots
      params:
        velocity_scale: 1.2
    execution:
      enabled: true
    hardware:
      enabled: true
```

---

## Next Steps

- **Configure boundaries** → [Boundary System](boundaries.md)
- **Learn about safety guarantees** → [Safety Guarantees](safety.md)
- **Deploy with Stackfiles** → [Quick Start Guide](../quick-stack.md)
- **Dive deeper** → [Full Specification](../DAM_Specification.md)
