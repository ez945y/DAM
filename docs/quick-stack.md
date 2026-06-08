# Quick Stack — Stackfile Reference Guide

A **Stackfile** is a YAML file that wires together all the components of a DAM deployment: hardware sources and sinks, a policy, guard parameters, safety boundaries, and tasks. You point DAM at a Stackfile and it handles everything else — connection lifecycle, observation assembly, guard orchestration, and hardware dispatch.

No Python code is required for a Tier 1 deployment (built-in guards + built-in adapters). Python callbacks and custom guards are opt-in for Tier 2 and Tier 3 deployments.

---

## Minimal Stackfile

The smallest valid Stackfile that runs the motion guard with a single boundary:

```yaml
version: "1"

guards:
  - L1: motion
    phase: 0
  - L2: execution
    phase: 1
  - L3: hardware
    always: true

safety:
  control_frequency_hz: 30
  enforcement_mode: enforce

boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]

tasks:
  demo:
    boundaries: [joint_position_limits]
```

To run it:

```bash
dam run my_stackfile.yaml --task demo
```

Or in Python:

```python
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("my_stackfile.yaml")
runtime.register_source("arm", my_source)
runtime.register_policy(my_policy)
runtime.register_sink(my_sink)
runtime.start_task("demo")

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
| `fallbacks` | object | no | built-ins | Named fallback strategies. Each entry references a registered `@dam.fallback` type. |
| `boundaries` | object | no | `{}` | Named boundary containers |
| `tasks` | object | no | `{}` | Named tasks referencing boundary containers |
| `safety` | object | no | see below | Global safety settings |
| `runtime` | object | no | see below | Control loop settings |
| `loopback` | object | no | — | MCAP loopback buffer (Phase 2, requires Rust) |
| `risk_controller` | object | no | — | Windowed risk aggregation (Phase 2, requires Rust) |
| `simulation` | object | no | — | Optional simulation/source settings |

### `safety` section defaults

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `always_active` | string or list[string] | `[]` | Boundary container name(s) active in all tasks |
| `no_task_behavior` | string | `"emergency_stop"` | Fallback when no task is active |
| `control_frequency_hz` | float | `30.0` | Target control loop frequency |
| `max_obs_age_sec` | float | `0.1` | Maximum observation age before stale warning |
| `cycle_budget_ms` | float | `20.0` | Per-cycle time budget; excess triggers watchdog |

### `fallbacks` section

Fallback implementations are registered in Python with `@dam.fallback`, independently
from boundary callbacks. The Stackfile defines named fallback strategies and boundary
nodes reference those names.

```yaml
fallbacks:
  emergency_stop:
    type: emergency_stop
  hold_position:
    type: hold_position
  slow_payload:
    type: slow_down
    escalate_to: emergency_stop
    escalate_after_seconds: 5.0
    params:
      scale: 0.5
```

Built-in context types are `emergency_stop`, `hold_position`, `wait_and_retry`,
`slow_down`, and `retreat`. Fallback Contexts can opt into L3 hardware monitoring
with `monitors_hardware`; `emergency_stop` is terminal and disables it.

### `runtime` section defaults

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `"passive"` | `"managed"` (built-in loop) or `"passive"` (caller drives `step()`) |
| `control_frequency_hz` | float | `30.0` | Target frequency for managed mode |
| `max_obs_age_sec` | float | `0.1` | Stale observation threshold |
| `cycle_budget_ms` | float | `20.0` | Cycle budget in managed mode |

---

## Guard Builtin Reference

### `guards.builtin.motion` (L1)

Enforces joint position limits, velocity limits, acceleration limits, and workspace bounds. All constraints are fused via a single QP solve that finds the least-perturbing safe action.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |
| `upper` | list[float] | required | Joint upper limits [rad], one per joint |
| `lower` | list[float] | required | Joint lower limits [rad], one per joint |
| `max_velocities` | list[float] | `null` | Per-joint velocity limits [rad/s] (`joint_velocity_limit`) |
| `max_acceleration` | list[float] | required | Per-joint acceleration limits [rad/s²] (`joint_acceleration_limit`) |
| `bounds` | list[list[float]] | `null` | `[[xmin, xmax], [ymin, ymax], [zmin, zmax]]` in metres |
| `params.velocity_scale` | float | `1.0` | Scale factor applied on top of hardware preset limits (Phase 2) |

**Clamping behaviour:**
- Joint positions outside limits are clamped to the nearest limit.
- Velocities exceeding `max_velocities` are clamped per-joint (`joint_velocity_limit`).
- Acceleration violations clamp velocity change per-joint (`joint_acceleration_limit`, separate boundary).
- Workspace violations always result in REJECT (cannot clamp the end-effector back into bounds).

### `guards.builtin.ood` (L0)

Out-of-distribution gate. Checks the reconstruction error of the full observation against the training distribution. High reconstruction error indicates the robot is in an unfamiliar state and the policy output cannot be trusted.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |
| `params.reconstruction_threshold` | float | `0.05` | Maximum allowed reconstruction error |
| `params.temporal_smoothing_frames` | int | `3` | Consecutive OOD frames required before rejecting; absorbs lighting, shadow, and brief occlusion false positives |

### `guards.builtin.execution` (L2)

Task-level boundary enforcement. Dispatches the active boundary node's L2 callback and enforces node timeouts.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable this guard |

Checks (in order):
1. `callback` — calls the node's registered L2 boundary callback; rejects on violation
2. `timeout_sec` — rejects if the node has been active longer than the timeout

Built-in L2 callbacks include `task_joint_speed_limit`, `task_workspace_bounds`,
`check_gripper_clear`, and `task_gripper_command_guard` (clamps incompatible
gripper commands to no-op). For pick-and-place, the default stack uses a
left-to-right `task_gripper_sequence` list: close is allowed only in a 15 cm
left pick cube, transfer allows no open/close command, and open is allowed only
in a 15 cm right place cube about 20 cm away.

L2 results are emitted as `/dam/L2` guard messages with `event_class: "task"`.
List containers fan out per active boundary/node name, so replay and the console
can show which task phase produced the result.

---

## Boundary Container Types

A boundary defines the safety envelope active during a task. Boundaries consist of **nodes** grouped in **containers** of one of three types.

### `single` — one static node

The simplest container. Holds one node that is active for the entire task.

```yaml
boundaries:
  safe_idle:
    layer: L1
    type: single
    nodes:
      - callback: joint_velocity_limit
        params:
          max_velocities: [0.05, 0.05, 0.05, 0.05, 0.05, 0.05]
        fallback: emergency_stop
```

### `list` — sequential node progression

Nodes are activated in order. The runtime advances to the next node by calling `runtime.advance_container("name")`. Useful for multi-phase tasks (reach → grasp → lift → place).

```yaml
boundaries:
  pick_place_approach:
    layer: L2
    type: list
    loop: false   # if true, wraps back to node 0 after the last node
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds:
            - [-0.35, 0.35]
            - [-0.05, 0.45]
            - [0.01, 0.40]
        fallback: hold_position
        timeout_sec: 15.0

      - callback: task_workspace_bounds
        params:
          bounds:
            - [-0.20, 0.20]
            - [0.05, 0.35]
            - [0.01, 0.15]
        fallback: hold_position
        timeout_sec: 8.0

      - callback: task_joint_speed_limit
        params:
          max_speed: 0.15
        fallback: hold_position
        timeout_sec: 10.0
```

### `graph` — arbitrary DAG transitions

Nodes form a directed graph. Transitions are triggered programmatically. Requires Python setup (not supported via `from_stackfile` yet — use `list` for sequential multi-phase tasks until Phase 3).

```yaml
boundaries:
  recovery_graph:
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
```

### Node fields

| Field | Type | Description |
|-------|------|-------------|
| `callback` | string | Built-in or registered callback name to evaluate |
| `params` | object | Callback-specific settings such as speed limits or workspace bounds |
| `fallback` | string | Optional named fallback strategy to use when the node violates |
| `timeout_sec` | float | Optional maximum time a node may remain active |

### Common `params` fields

| Field | Type | Description |
|-------|------|-------------|
| `max_speed` | float | Maximum joint velocity norm [rad/s] |
| `max_velocities` | list[float] | Per-joint velocity limit [rad/s] |
| `bounds` | list[list[float]] | `[[xmin, xmax], [ymin, ymax], [zmin, zmax]]` [m] |
| `upper` | list[float] | Per-joint position upper limit [rad] |
| `lower` | list[float] | Per-joint position lower limit [rad] |
| `max_force_n` | float | Maximum force norm [N] |

### Fallback strategies (per node)

| Name | Behaviour |
|------|-----------|
| `emergency_stop` | Immediately stop all motion. Sets hardware E-Stop if available. |
| `hold_position` | Command the robot to hold its current joint positions. |
| `retreat` | Move at low speed along a predefined retreat trajectory. |

---

## Hardware Section

The `hardware` section declares physical or virtual hardware interfaces.

### LeRobot example (SO-ARM101)

```yaml
hardware:
  preset: so101_follower    # auto-loads joint names and factory limits
  input_space: joint         # joint (default) or ee; must match policy.input_space

  joints:                   # optional calibration overrides
    shoulder_pan:
      limits_rad: [-2.09, 2.09]
    gripper:
      limits_rad: [0.0, 0.044]

  sources:
    follower_arm:
      type: motor
      port: /dev/tty.usbmodem5AA90244141
      robot_type: so101_follower
      id: my_follower_arm

    top_cam:
      type: opencv
      index_or_path: 0
      width: 640
      height: 480
      fps: 30

  sinks:
    follower_command:
      ref: sources.follower_arm   # bidirectional — same robot instance
```

OpenCV camera options are source-level fields. Put `index_or_path`, `width`, `height`,
and `fps` directly under the camera source; `params:` is reserved for boundary and
guard configuration.

`hardware.input_space` declares the action space expected by the hardware or
adapter layer. Valid values are `joint` and `ee`; the default is `joint`. When a
Stackfile also declares `policy.input_space`, both values must match or
validation fails. Current runners and sinks still dispatch validated joint
targets; EE-space actions require an IK/FK resolver at the API or adapter
boundary before they can be enforced.

For a LeRobot motor source, `robot_type` selects the concrete LeRobot robot
configuration and its default calibration namespace. For `so101_follower`,
the default calibration directory is
`~/.cache/huggingface/lerobot/calibration/robots/so101_follower/`, and the
configured `id` selects the JSON file within that directory. Set
`calibration_path` only when using an explicit alternate calibration location.

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

## Policy Section

The `policy` section declares the action producer used by managed runners.

```yaml
policy:
  type: act
  pretrained_path: MikeChenYZ/act-soarm-fmb-v2
  device: cpu
  input_space: joint         # joint (default) or ee; must match hardware.input_space
```

Use `input_space: joint` for policies that emit joint targets. `input_space: ee`
is reserved for policies that emit end-effector poses
`[x, y, z, qx, qy, qz, qw]`; DAM validates the declaration today, but EE action
conversion must be provided by an IK/FK-capable integration before execution.

---

## Loading a Stackfile in Python

### High-level (with LeRobot runner)

```python
from dam.runner.lerobot import LeRobotRunner

# build_from_stackfile automates registry and adapter construction
runner = LeRobotRunner.from_stackfile("examples/stackfiles/so101.yaml")
runner.run("pick_and_place")  # runs managed loop until KeyboardInterrupt
```

### Low-level (GuardRuntime directly)

```python
from dam.runtime.guard_runtime import GuardRuntime

runtime = GuardRuntime.from_stackfile("my_stackfile.yaml")

# Register your adapters (named)
runtime.register_source("arm", my_source_adapter)
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

### Programmatic construction

Prefer Stackfiles for normal use. They are easier to inspect, validate, review,
and share than hand-built runtime objects. Use the low-level Python APIs only
when you are writing DAM itself, building a test fixture, or adding a new
adapter/guard implementation.

---

## Loopback Logging (MCAP)

Configure continuous recording of cycle records (observations, actions, guard results) to MCAP files for post-mortem analysis and incident investigation.

```yaml
loopback:
  backend: mcap                    # "mcap" (recommended) or "pickle"
  output_dir: /data/robot/sessions # Directory for session files
  window_sec: 10.0                 # Ring buffer depth for violation context
  capture_on_violation: true       # Always on (controls image capture)
  rotate_mb: 500.0                 # Rotate file every 500 MB
  rotate_minutes: 60.0             # Or every 60 minutes (whichever comes first)
  max_queue_depth: 256             # Records buffered before drop (never blocks main loop)
  capture_images_on_clamp: false   # Also capture images on CLAMP? (expensive; default off)
```

**Key settings:**

- `output_dir`: Ensure the directory exists and is writable. Paths are created on first write.
- `window_sec`: Increases buffer depth for images. Example: 30 s ≈ 1500 frames + cameras at 50 Hz = ~10–30 MB per violation.
- `rotate_mb` / `rotate_minutes`: File rotation policy. E.g. 500 MB + 60 min = whichever comes first.
- `capture_images_on_clamp`: Default off because motion clamps can be frequent (boundary probing). Enable only for detailed debugging.

**Output:** Each session is a single `.mcap` file containing:
- `/dam/cycle` — control loop summary (pass / clamp / reject / fault)
- `/dam/obs` — sensor state (joint angles, EE pose, force/torque)
- `/dam/action` — proposal and validated action
- `/dam/L0` … `/dam/L3` — per-layer guard results
- `/dam/latency` — per-layer latency aggregates
- `/dam/images/{cam}` — camera frames (on violation or clamp)

See [Loopback Logging](loopback-logging.md) for full schema, API, and analysis tools.

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
dam validate my_stackfile.yaml
```

CI automatically validates all Stackfiles under `examples/stackfiles/` on every push.
