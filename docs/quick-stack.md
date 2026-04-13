# Quick Stack — Stackfile Reference Guide

A **Stackfile** is a YAML file that wires together all the components of a DAM deployment: hardware sources and sinks, a policy, guard parameters, safety boundaries, and tasks. You point DAM at a Stackfile and it handles everything else — connection lifecycle, observation assembly, guard orchestration, and hardware dispatch.

No Python code is required for a Tier 1 deployment (built-in guards + built-in adapters). Python callbacks and custom guards are opt-in for Tier 2 and Tier 3 deployments.

---

## Minimal Stackfile

The smallest valid Stackfile that runs the motion guard with a single boundary:

```yaml
dam:
  version: "1"

guards:
  builtin:
    motion:
      enabled: true
      upper_limits: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
      lower_limits: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]
      max_velocity:    [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
      max_acceleration:[3.0, 3.0, 3.0, 3.0, 3.0, 1.0]

boundaries:
  always_active: default
  containers:
    default:
      type: single
      node:
        node_id: default
        constraint: {}
```

To run it:

```bash
dam run --stack my_stackfile.yaml --task default
```

Or in Python:

```python
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("my_stackfile.yaml")
runtime.register_source(my_source)
runtime.register_policy(my_policy)
runtime.register_sink(my_sink)
runtime.start_task("default")

for _ in range(100):
    result = runtime.step()
```

---

## Full Field Reference

Top-level keys accepted by a Stackfile:

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `version` | string | no | `"1"` | Stackfile schema version |
| `hardware` | object | no | — | Hardware sources, sinks, and joint presets |
| `policy` | object | no | — | Policy adapter type and parameters |
| `guards` | object | no | `{}` | Guard enable flags and parameters |
| `boundaries` | object | no | `{}` | Named boundary containers |
| `tasks` | object | no | `{}` | Named tasks referencing boundary containers |
| `safety` | object | no | see below | Global safety settings |
| `runtime` | object | no | see below | Control loop settings |
| `loopback` | object | no | — | MCAP loopback buffer (Phase 2, requires Rust) |
| `risk_controller` | object | no | — | Windowed risk aggregation (Phase 2, requires Rust) |
| `simulation` | object | no | — | Physics simulator for L1 Sim Preflight (Phase 4) |

### `safety` section defaults

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `always_active` | string or list[string] | `[]` | Boundary container name(s) active in all tasks |
| `no_task_behavior` | string | `"emergency_stop"` | Fallback when no task is active |
| `control_frequency_hz` | float | `50.0` | Target control loop frequency |
| `max_obs_age_sec` | float | `0.1` | Maximum observation age before stale warning |
| `cycle_budget_ms` | float | `20.0` | Per-cycle time budget; excess triggers watchdog |

### `runtime` section defaults

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `"passive"` | `"managed"` (built-in loop) or `"passive"` (caller drives `step()`) |
| `control_frequency_hz` | float | `50.0` | Target frequency for managed mode |
| `max_obs_age_sec` | float | `0.1` | Stale observation threshold |
| `cycle_budget_ms` | float | `20.0` | Cycle budget in managed mode |

---

## Guard Builtin Reference

### `guards.builtin.motion` (L2)

Enforces joint position limits, velocity limits, and acceleration limits. Clamps action proposals rather than rejecting them when possible. Rejects when the end-effector is outside workspace bounds.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |
| `upper_limits` | list[float] | required | Joint upper limits [rad], one per joint |
| `lower_limits` | list[float] | required | Joint lower limits [rad], one per joint |
| `max_velocity` | list[float] | `null` | Per-joint velocity limits [rad/s] |
| `max_acceleration` | list[float] | `null` | Per-joint acceleration limits [rad/s²] |
| `bounds` | list[list[float]] | `null` | `[[xmin, xmax], [ymin, ymax], [zmin, zmax]]` in metres |
| `params.velocity_scale` | float | `1.0` | Scale factor applied on top of hardware preset limits (Phase 2) |

**Clamping behaviour:**
- Joint positions outside limits are clamped to the nearest limit.
- Velocities exceeding `max_velocity` are scaled proportionally (all joints scaled by the same ratio).
- Acceleration violations scale the target velocity back so the implied acceleration stays within limits.
- Workspace violations always result in REJECT (cannot clamp the end-effector back into bounds).

### `guards.builtin.ood` (L0)

Out-of-distribution gate. Checks the reconstruction error of the full observation against the training distribution. High reconstruction error indicates the robot is in an unfamiliar state and the policy output cannot be trusted.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |
| `params.reconstruction_threshold` | float | `0.05` | Maximum allowed reconstruction error |

### `guards.builtin.execution` (L3)

Task-level boundary enforcement. Evaluates the constraints on the currently active boundary node each cycle.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |

Checks (in order):
1. `max_speed` — rejects if joint velocity norm exceeds the limit
2. `bounds` — rejects if end-effector is outside bounds
3. `max_force_n` — rejects if force/torque norm exceeds the limit
4. `callback` — calls each registered Python callback; rejects if any returns `False`
5. `timeout_sec` — rejects if the node has been active longer than the timeout

---

## Boundary Container Types

A boundary defines the safety envelope active during a task. Boundaries consist of **nodes** grouped in **containers** of one of three types.

### `single` — one static node

The simplest container. Holds one node that is active for the entire task.

```yaml
boundaries:
  safe_idle:
    type: single
    nodes:
      - node_id: idle
        constraint:
          max_speed: 0.05
        fallback: emergency_stop
```

### `list` — sequential node progression

Nodes are activated in order. The runtime advances to the next node by calling `runtime.advance_container("name")`. Useful for multi-phase tasks (reach → grasp → lift → place).

```yaml
boundaries:
  pick_place_approach:
    type: list
    loop: false   # if true, wraps back to node 0 after the last node
    nodes:
      - node_id: reach
        constraint:
          max_speed: 0.3
          bounds:
            - [-0.35, 0.35]
            - [-0.05, 0.45]
            - [0.01, 0.40]
        fallback: hold_position
        timeout_sec: 15.0

      - node_id: grasp
        constraint:
          max_speed: 0.08
          bounds:
            - [-0.20, 0.20]
            - [0.05, 0.35]
            - [0.01, 0.15]
        fallback: hold_position
        timeout_sec: 8.0

      - node_id: lift
        constraint:
          max_speed: 0.15
        fallback: hold_position
        timeout_sec: 10.0
```

### `graph` — arbitrary DAG transitions

Nodes form a directed graph. Transitions are triggered programmatically. Requires Python setup (not supported via `from_stackfile` yet — use `list` for sequential multi-phase tasks until Phase 3).

```yaml
boundaries:
  recovery_graph:
    type: graph
    nodes:
      - node_id: normal
        constraint:
          max_speed: 0.3
        fallback: hold_position
      - node_id: slow_recovery
        constraint:
          max_speed: 0.05
        fallback: emergency_stop
```

### Constraint fields (per node)

| Field | Type | Description |
|-------|------|-------------|
| `max_speed` | float | Maximum joint velocity norm [rad/s] |
| `max_velocity` | list[float] | Per-joint velocity limit [rad/s] |
| `bounds` | list[list[float]] | `[[xmin, xmax], [ymin, ymax], [zmin, zmax]]` [m] |
| `upper_limits` | list[float] | Per-joint position upper limit [rad] |
| `lower_limits` | list[float] | Per-joint position lower limit [rad] |
| `max_force_n` | float | Maximum force norm [N] |
| `callback` | list[string] | Python callback names registered with `@dam.callback` |

### Fallback strategies (per node)

| Name | Behaviour |
|------|-----------|
| `emergency_stop` | Immediately stop all motion. Sets hardware E-Stop if available. |
| `hold_position` | Command the robot to hold its current joint positions. |
| `safe_retreat` | Move at low speed along a predefined retreat trajectory. |

---

## Hardware Section

The `hardware` section declares physical or virtual hardware interfaces.

### LeRobot example (SO-ARM101)

```yaml
hardware:
  preset: so101_follower    # auto-loads joint names and factory limits

  joints:                   # optional calibration overrides
    shoulder_pan:
      limits_rad: [-2.09, 2.09]
    gripper:
      limits_rad: [0.0, 0.044]

  sources:
    follower_arm:
      type: lerobot
      port: /dev/tty.usbmodem5AA90244141
      id: my_follower_arm
      cameras:
        top:
          type: opencv
          index: 0
          width: 640
          height: 480
          fps: 30

  sinks:
    follower_command:
      ref: sources.follower_arm   # bidirectional — same robot instance
```

### ROS2 example

```yaml
hardware:
  sources:
    joint_states:
      type: ros2
      topic: /joint_states
      msg_type: sensor_msgs/JointState
      mapping:
        joint_positions: position
        joint_velocities: velocity

  sinks:
    joint_commands:
      type: ros2
      topic: /joint_trajectory_controller/joint_trajectory
      msg_type: trajectory_msgs/JointTrajectory
```

---

## Loading a Stackfile in Python

### High-level (with LeRobot runner)

```python
from dam.runner.lerobot import LeRobotRunner

runner = LeRobotRunner.from_stackfile("examples/stackfiles/so101_act_pick_place.yaml")
runner.start_task("pick_and_place")
runner.run()    # managed loop at 50 Hz until KeyboardInterrupt
```

### Low-level (GuardRuntime directly)

```python
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("my_stackfile.yaml")

# Register your adapters (duck-typed)
runtime.register_source(my_source_adapter)
runtime.register_policy(my_policy_adapter)
runtime.register_sink(my_sink_adapter)

# Start a task (activates its boundary containers)
runtime.start_task("pick_and_place")

# Step manually (passive mode)
for _ in range(n_cycles):
    result = runtime.step()
    print(result.risk_level, result.was_clamped, result.was_rejected)

runtime.stop_task()
```

### Programmatic construction (no Stackfile)

```python
import numpy as np
from dam.runtime.guard_runtime import GuardRuntime
from dam.guard.builtin.motion import MotionGuard
from dam.decorators import guard as guard_decorator
from dam.fallback.registry import FallbackRegistry
from dam.fallback.builtin import EmergencyStop, HoldPosition, SafeRetreat
from dam.fallback.chain import build_escalation_chain
from dam.boundary.node import BoundaryNode
from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.single import SingleNodeContainer

# Decorate and instantiate the guard
MotionGuard = guard_decorator("L2")(MotionGuard)
motion_guard = MotionGuard()

# Set up fallback registry
fallback_registry = FallbackRegistry()
fallback_registry.register(EmergencyStop())
fallback_registry.register(HoldPosition())
fallback_registry.register(SafeRetreat())
build_escalation_chain(fallback_registry)

# Set up a boundary
constraint = BoundaryConstraint(max_speed=0.3)
node = BoundaryNode(node_id="default", constraint=constraint, fallback="hold_position")
container = SingleNodeContainer(node)

runtime = GuardRuntime(
    guards=[motion_guard],
    boundary_containers={"main": container},
    fallback_registry=fallback_registry,
    task_config={"demo": ["main"]},
    config_pool={
        "upper_limits": np.array([1.57, 1.57, 1.57, 1.57, 1.57, 0.08]),
        "lower_limits": np.array([-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]),
    },
)
```

---

## Hot Reload

DAM can reload boundary constraints and guard parameters from a modified Stackfile without stopping the control loop. Changes are applied atomically at the start of the next cycle.

```python
from dam.config.hot_reload import StackfileWatcher

watcher = StackfileWatcher(
    path="my_stackfile.yaml",
    on_change=runtime.apply_pending_reload,
    poll_interval_s=0.5,
)
watcher.start()

# Edit my_stackfile.yaml on disk — changes take effect within ~0.5s
# ...

watcher.stop()
```

Only static config-pool parameters (guard limits, boundary constraints) are reloaded. The guard class structure and task definitions are not changed at runtime.

---

## Stackfile Validation

Validate a Stackfile against the schema without running the control loop:

```bash
dam validate --stack my_stackfile.yaml
```

CI automatically validates all Stackfiles under `examples/stackfiles/` on every push.
