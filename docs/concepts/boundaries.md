# Boundary System

**Boundaries** define named safety rules in a Stackfile. A boundary attaches to a guard layer such as L0, L1, L2, or L3, names the callback to run, and provides the parameters for that check.

---

## First-Read Path

If you are new to DAM, start with [Stackfile Walkthrough](../getting-started/stackfile-walkthrough.md) and [Common Stackfile Edits](../getting-started/common-stackfile-edits.md). Use this page after you understand how a task activates named boundaries.

---

## What Are Boundaries?

Boundaries are YAML-defined checks that constrain robot behavior during specific tasks or always-on safety monitoring.

```yaml
boundaries:
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.4000, 0.4000], [-0.4000, 0.4000], [0.0200, 0.6000]]

tasks:
  demo:
    boundaries: [workspace]
```

**Key idea:** Instead of hard-coding safety checks in your policy, you **parameterize** them in a Stackfile. This lets you:
- ✅ Update constraints without retraining
- ✅ Enable/disable task phases dynamically
- ✅ Hot-reload on a running robot
- ✅ Audit and understand task constraints

---

## Container Types

DAM supports three types of boundary containers:

### 1. Single (Static Boundary)

A **single** node is active for the **entire task**.

```yaml
boundaries:
  idle:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.1, 0.1], [0.2, 0.3], [0.0, 0.2]]
        fallback: emergency_stop
```

**Use cases:**
- Idle/holding position
- Teleoperation (fixed safety zone)
- Maintenance mode

### 2. List (Sequential Phases)

A **list** contains multiple nodes. The runtime **advances** to the next node explicitly.

```yaml
boundaries:
  pick_and_place:
    layer: L2
    type: list
    loop: false      # If true, wraps back to node 0 after last node
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0

      - callback: task_workspace_bounds
        params:
          bounds: [[-0.20, 0.20], [0.05, 0.35], [0.01, 0.15]]
        fallback: hold_position
        timeout_sec: 8.0

      - callback: task_joint_speed_limit
        params:
          max_speed: 0.15
        fallback: hold_position
        timeout_sec: 10.0
```

**Activation:**

```python
runtime.start_task("pick_and_place")  # Starts at the first node
# ... control loop runs ...
runtime.advance_container("pick_and_place")  # Move to the next node
# ... control loop runs ...
runtime.advance_container("pick_and_place")  # Move to the final node
```

**Use cases:**
- Multi-phase tasks (reach → grasp → lift → place)
- Sequential manipulation
- Progressive task execution

### 3. Graph (Arbitrary DAG)

A **graph** allows **arbitrary transitions** between nodes. Nodes form a directed acyclic graph (DAG).

```yaml
boundaries:
  recovery:
    layer: L2
    type: graph
    nodes:
      - callback: task_joint_speed_limit
        params:
          max_speed: 0.3
        fallback: hold_position

      - callback: task_joint_speed_limit
        params:
          max_speed: 0.05
        fallback: emergency_stop

      - callback: task_joint_speed_limit
        params:
          max_speed: 0.0
        fallback: emergency_stop
```

**Activation (Python only, not yet supported via Stackfile):**

```python
runtime.start_task("recovery")         # Start at "normal"
# ... control loop ...
runtime.transition_to("recovery", "error_recovery")  # Jump to error_recovery
# ... control loop ...
runtime.transition_to("recovery", "shutdown")       # Move to shutdown
```

**Use cases:**
- Error recovery flows
- Dynamic task rescheduling
- State machine-based tasks

---

## Node Checks

Each node names a **callback** and passes that callback a `params` object. The
boundary's `layer` decides which guard layer runs the check.

### Common Callback Parameters

| Parameter | Used by | Example | Behavior |
|-----------|---------|---------|----------|
| `max_speed` | `task_joint_speed_limit` | `0.3` | Reject if joint velocity norm exceeds the task limit |
| `max_velocities` | `joint_velocity_limit` | `[1.0, 1.0, ...]` | Clamp per-joint velocity proposals to the limit |
| `bounds` | `workspace`, `task_workspace_bounds` | `[[-0.5, 0.5], ...]` | Keep or reject motion outside the workspace box, depending on layer |
| `upper` / `lower` | `joint_position_limits` | `[1.57, ...]` | Clamp or reject joint positions outside the configured limits |

### Example: Full Node

```yaml
boundaries:
  manipulation:
    layer: L2
    type: single
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 20.0
```

### Evaluation Order

The guard layer evaluates the active node's callback, then applies node-level
settings such as `timeout_sec`.

1. **callback** — registered boundary callback
2. **timeout_sec** — node active duration

```python
# Pseudocode
def evaluate_node(obs, node):
    result = callbacks[node.callback](obs=obs, **node.params)
    if not result.ok:
        return result

    if node.timeout_sec and node.active_time > node.timeout_sec:
        return REJECT

    return PASS
```

---

## Fallback Strategies

When a boundary constraint is violated, what happens? That's determined by the **fallback strategy**.

### Available Fallbacks

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `hold_position` | Command zero velocity; stay put | Normal violations |
| `retreat` | Move at low speed along predefined retreat path | Error recovery |
| `emergency_stop` | Stop all motion immediately; activate E-Stop | Critical failures |

### Configuration

```yaml
boundaries:
  reach:
    layer: L2
    type: list
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position      # Hold if the node violates

      - callback: task_workspace_bounds
        params:
          bounds: [[-0.10, 0.10], [0.10, 0.30], [0.02, 0.20]]
        fallback: emergency_stop     # E-Stop if we get near fragile object
```

### Fallback Escalation

DAM can chain fallbacks: if the first fallback fails, escalate to the next.

```python
from dam.fallback.chain import build_escalation_chain

fallback_registry = FallbackRegistry()
fallback_registry.register(HoldPosition())
fallback_registry.register(SafeRetreat(retreat_joint_positions=[...]))
fallback_registry.register(EmergencyStop())

build_escalation_chain(fallback_registry)
# Chain: hold → retreat → e-stop
```

---

## Workspace Bounds (Common Pattern)

The most common boundary is **workspace bounds**: a 3D box the end-effector should stay inside.

```yaml
boundaries:
  table_workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          # Workspace is 70 cm wide, 50 cm deep, 40 cm tall.
          bounds:
            - [-0.35, 0.35]        # x: ±35 cm
            - [-0.05, 0.45]        # y: 5–45 cm
            - [0.01, 0.40]         # z: 1–40 cm
        fallback: hold_position
```

**How bounds are checked:**
1. Compute end-effector position via forward kinematics
2. Check if position is inside `[x_min..x_max, y_min..y_max, z_min..z_max]`
3. If outside, the selected layer responds: L1 can halt or clamp, while L2 rejects the task action.

**Coordinate system:** Relative to the robot base (usually the mount point).

---

## Multi-Boundary Tasks

A task can activate **multiple boundaries simultaneously**. They all apply.

```yaml
tasks:
  complex_manipulation:
    boundaries:
      - workspace_limits    # Always active
      - safety_zone         # Always active
      - task_specific_reach # Phase-dependent
```

**Evaluation:** All active boundaries are checked. If **any** rejects, the action is rejected.

---

## Task Activation

Tasks are the **entry point** for boundary execution. A task references one or more boundary containers.

```yaml
tasks:
  pick_and_place:
    boundaries:
      - pick_and_place         # Main task boundary
      - always_safe_zone       # Always active (workspace limit)

  idle:
    boundaries:
      - idle
```

**Starting a task:**

```python
runtime.start_task("pick_and_place")
# Now the boundary containers ["pick_and_place", "always_safe_zone"] are active
```

---

## Advanced: Custom Callbacks

For checks beyond built-in position, velocity, workspace, and hardware limits,
add a boundary callback and reference it from a Stackfile.

### Define a Callback

```python
from dam.boundary.registry import boundary_callback
from dam.guard.pipeline import CallbackResult
from dam.types.observation import Observation

@boundary_callback(
    name="force_limited_grasp",
    layer="L2",
    description="Rejects when contact force exceeds the configured limit.",
    params={"max_force_n": "Maximum allowed contact force in newtons."},
)
def force_limited_grasp(*, obs: Observation, max_force_n: float = 30.0) -> CallbackResult:
    force_norm = obs.metadata.get("force_norm")
    if force_norm is None or float(force_norm) <= max_force_n:
        return CallbackResult.ok("force_limited_grasp")
    return CallbackResult.violate("force_limited_grasp", "force limit exceeded")
```

### Register in Stackfile

```yaml
boundaries:
  grasp_phase:
    layer: L2
    type: single
    nodes:
      - callback: force_limited_grasp
        params:
          max_force_n: 30.0
        fallback: hold_position
        timeout_sec: 5.0
```

### Callback Signature

```python
def my_callback(*, obs: Observation, my_param: float = 1.0) -> CallbackResult:
    """
    Parameters:
      obs: Current observation (sensor readings)
      my_param: A value supplied from Stackfile params

    Returns:
      CallbackResult.ok(...): check passed
      CallbackResult.violate(...): check failed
    """
    ...
```

---

## Design Patterns

### Always-On Safety Zone + Task Boundaries

Combine a global L1 workspace limit (always active) with task-specific L2 boundaries:

```yaml
boundaries:
  global_safety:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.5, 0.5], [-0.2, 0.6], [-0.1, 1.5]]
        fallback: emergency_stop

  main_task:
    layer: L2
    type: list
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: retreat

tasks:
  pick_and_place:
    boundaries: [global_safety, main_task]
```

### Error Recovery

Chain a low-speed recovery boundary alongside the main task:

```yaml
boundaries:
  error_recovery:
    layer: L2
    type: single
    nodes:
      - callback: task_joint_speed_limit
        params:
          max_speed: 0.05
        fallback: hold_position

tasks:
  with_recovery:
    boundaries: [main_task, error_recovery]
```

---

## Debugging Boundaries

### Validate Stackfile

```bash
dam validate mystack.yaml
```

### Inspect Active Boundaries

```python
runtime.start_task("my_task")
active = runtime.get_active_boundaries()
print(active)  # ["boundary_1", "boundary_2", ...]
```

### Log Boundary Violations

```python
result = runtime.step()
if result.was_rejected:
    print(f"Rejected by guard: {result.rejecting_guard}")
    print(f"Reason: {result.decision_reason}")
```

### Replay MCAP Buffer

```bash
# Export violations to JSON
curl http://localhost:8080/api/risk-log/export/json > violations.json

# Analyze with mcap CLI
mcap cat violations.mcap | jq '.[] | select(.rejecting_guard == "L3")'
```

---

## Tips

| Tip | Why |
|-----|-----|
| Start with tight bounds, loosen incrementally | Easier to relax constraints than debug a crash |
| Combine global L1 + task-specific L2 boundaries | Defense in depth |
| Test fallbacks in simulation first | Verify hold/retreat/e-stop before hardware |
| Keep callbacks under 5 ms | Complex logic belongs in the policy, not the guard |
| Watch clamp rates | High rates often mean bounds are too tight or calibration is off |
| Version your Stackfiles | Track which boundaries worked for which tasks |

---

## Next Steps

- [Guard Stack Explained](guards-explained.md) -- how guards evaluate boundaries
- [Boundary Callbacks](../boundary-callbacks.md) -- callback catalog and custom callback API
- [Common Stackfile Edits](../getting-started/common-stackfile-edits.md) -- quick config recipes
- [Stackfile Reference](../quick-stack.md) -- full field reference
