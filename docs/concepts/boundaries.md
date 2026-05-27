# Boundary System

A boundary is a named safety rule in your Stackfile. It tells DAM: "run this check, with these parameters, on this guard layer." You define boundaries once, attach them to tasks, and DAM enforces them every cycle.

```yaml
boundaries:
  joint_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]

tasks:
  demo:
    boundaries: [joint_limits]
```

This activates `joint_position_limits` on the L1 guard whenever the `demo` task runs. If the policy proposes a joint position outside the limits, L1 clamps it.

---

## Anatomy of a Boundary Node

Each node has four fields:

| Field | Required | What it does |
|-------|----------|-------------|
| `callback` | yes | Name of a registered check (`dam callbacks` lists all 18) |
| `params` | yes | Parameters passed to the callback |
| `fallback` | no | What to do on rejection: `hold_position`, `retreat`, or `emergency_stop` (default: `emergency_stop`) |
| `timeout_sec` | no | Reject if this node stays active longer than N seconds |

The callback does the actual work. The boundary just names it and feeds it parameters.

---

## 18 Built-in Callbacks

Run `.venv/bin/dam callbacks` to see all of them. Grouped by layer:

**L0 -- Out-of-Distribution**

| Callback | Behavior |
|----------|----------|
| `ood_detector` | Scores observation against trained distribution. Rejects on high anomaly score. |

**L1 -- Physical Kinematics**

| Callback | Behavior |
|----------|----------|
| `joint_position_limits` | **Clamps** joints to `[lower, upper]` |
| `joint_velocity_limit` | **Clamps** velocities to `±max_velocities` (scales all joints proportionally) |
| `workspace` | **Rejects** if end-effector leaves the workspace box |
| `cartesian_velocity_limit` | **Rejects** if end-effector linear/angular speed exceeds limit |
| `keep_out_zone` | **Rejects** if end-effector enters a forbidden box or sphere |
| `orientation_limit` | **Rejects** if end-effector tilt from reference axis exceeds limit |
| `base_geofence` | **Rejects** if mobile base leaves a geofence polygon |
| `check_joints_not_moving` | **Rejects** if any joint velocity exceeds a near-zero threshold |
| `check_velocity_smooth` | **Rejects** if velocity jerk exceeds threshold |

**L2 -- Task Execution**

| Callback | Behavior |
|----------|----------|
| `task_gripper_command_guard` | **Clamps** gripper commands incompatible with active task node |

**L3 -- Hardware Monitoring**

| Callback | Behavior |
|----------|----------|
| `temperature_limit` | **Rejects** if motor temperature exceeds threshold |
| `current_limit` | **Rejects** if motor current exceeds threshold |
| `voltage_limit` | **Rejects** if supply voltage outside safe band |
| `force_limit` | **Rejects** if force magnitude exceeds threshold |
| `check_force_torque_safe` | **Rejects** if force or torque exceeds thresholds |
| `hardware_watchdog` | **Rejects** if observations go stale |
| `host_health_limit` | **Rejects** if host CPU/GPU/memory/temperature crosses limits |

Key distinction: L1 callbacks that have a well-defined correction (position, velocity) **clamp** the action. Callbacks where no clean correction exists (workspace, keep-out, geofence) **reject**.

---

## Container Types

A boundary has a `type` that controls how nodes are activated:

### `single` -- One node, always active

```yaml
boundaries:
  workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
```

Use for: always-on limits (joint bounds, workspace, hardware watchdog).

### `list` -- Sequential phases

Multiple nodes, advanced explicitly. Use `loop: true` to wrap back to node 0.

```yaml
boundaries:
  pick_and_place:
    layer: L2
    type: list
    loop: false
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0

      - callback: task_workspace_bounds
        params:
          bounds: [[-0.10, 0.10], [0.10, 0.30], [0.02, 0.20]]
        fallback: hold_position
        timeout_sec: 8.0
```

Advance in code:

```python
runtime.start_task("pick_and_place")   # starts at node 0
runtime.advance_container("pick_and_place")  # move to node 1
```

Use for: multi-phase tasks (reach, grasp, lift, place).

### `graph` -- Arbitrary transitions (Python only)

Nodes connected by conditional edges with priorities. Cannot be fully declared in YAML -- requires Python setup.

```python
from dam.boundary.graph import GraphContainer, Transition

graph = GraphContainer(
    nodes={"normal": node_a, "recovery": node_b, "shutdown": node_c},
    transitions={
        "normal": [Transition(to_node="recovery", condition=fault_detected)],
        "recovery": [Transition(to_node="shutdown", condition=unrecoverable)],
    },
    initial_node_id="normal",
)
```

Use for: error recovery, state-machine tasks.

---

## Tasks Activate Boundaries

A task references one or more boundaries by name. All referenced boundaries are checked every cycle. If **any** rejects, the action is rejected.

```yaml
boundaries:
  global_workspace:
    layer: L1
    type: single
    nodes:
      - callback: workspace
        params:
          bounds: [[-0.5, 0.5], [-0.2, 0.6], [-0.1, 1.5]]
        fallback: emergency_stop

  task_speed:
    layer: L2
    type: single
    nodes:
      - callback: task_joint_speed_limit
        params:
          max_speed: 0.3
        fallback: hold_position

tasks:
  pick_and_place:
    boundaries: [global_workspace, task_speed]
```

Pattern: global L1 boundaries for hard physical limits + task-scoped L2 boundaries for task-specific constraints.

---

## Writing Custom Callbacks

When the 18 built-ins don't cover your check, register a custom callback:

```python
from dam.boundary.callbacks._registry import boundary_callback
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

Then use it in a Stackfile like any built-in:

```yaml
boundaries:
  grasp_check:
    layer: L2
    type: single
    nodes:
      - callback: force_limited_grasp
        params:
          max_force_n: 30.0
        fallback: hold_position
```

Rules for custom callbacks:

- All user params come via `**kwargs`; runtime injects `obs`, `action`, `dt` automatically
- Return `CallbackResult.ok(name)` or `CallbackResult.violate(name, reason)`
- For clamp-style corrections, return `CallbackResult.clamp(name, validated_action)`
- Keep execution under 5 ms -- complex logic belongs in the policy

---

## Debugging

```bash
.venv/bin/dam validate mystack.yaml     # catch schema errors early
.venv/bin/dam callbacks                 # verify your callback is registered
```

In code:

```python
result = runtime.step()
if result.was_rejected:
    print(result.rejecting_guard, result.decision_reason)
```

In the console: watch clamp rates per boundary. A high clamp rate usually means bounds are too tight or calibration is off -- not that the policy is bad.

---

## Tips

| Do | Why |
|----|-----|
| Start tight, loosen incrementally | Easier to relax than debug a crash |
| Combine L1 global + L2 task boundaries | Defense in depth |
| Test fallbacks in sim before hardware | Verify hold/retreat/e-stop actually work |
| Watch clamp rates, not just rejections | Clamps tell you where the policy pushes limits |
| Version your Stackfiles | Track which boundaries worked for which tasks |

---

## Next Steps

- [Boundary Callbacks](../boundary-callbacks.md) -- full parameter reference for all 18 callbacks
- [Common Stackfile Edits](../getting-started/common-stackfile-edits.md) -- quick config recipes
- [Guard Stack Explained](guards-explained.md) -- how guards evaluate boundaries
- [Stackfile Reference](../quick-stack.md) -- complete field reference
