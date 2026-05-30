# Boundary Callbacks

Built-in check functions referenced by Stackfile boundary nodes.
These are ready-to-use callbacks that guard layers evaluate each cycle.

---

## Quick start

Reference callbacks by name in your Stackfile:

```yaml
boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453]
          lower: [-1.8243, -1.7691, -1.6026, -1.8067, -3.0741, 0]
  temperature_guard:
    layer: L3
    type: single
    nodes:
      - callback: temperature_limit
        fallback: emergency_stop
        params:
          max_temperature_c: 55.0
```

All callbacks are auto-registered via `register_all()` at runtime startup.

### Source layout

Built-ins live in the `dam.boundary.callbacks` package, **one module per
guard layer** — the sections below mirror it exactly:

| Module | Layer | Callbacks |
|---|---|---|
| `callbacks/ood.py` | L0 | `ood_detector` (choose backend with `params.backend`) |
| `callbacks/kinematics.py` | L1 | joint / workspace / Cartesian / keep-out / orientation / geofence |
| `callbacks/execution.py` | L2 | task speed / task workspace / gripper clear / gripper command guard |
| `callbacks/hardware.py` | L3 | watchdog, temperature / current / voltage / force |

Adding a `@boundary_callback` function to the matching module is all that is
needed — `register_all()` discovers it automatically (no list to maintain).

### Scenario and response guide

The callback detects the condition; the node's `fallback` chooses the
deployment response. This table documents the recommended built-in pairing
used by the templates, so operators can review intent rather than infer it
from a function name.

| Callback | Category | Scenario Detected | Recommended Response |
|---|---|---|---|
| `ood_detector` | anomaly | Observation no longer resembles calibrated normal operation | `hold_position` while the operator reviews context |
| `joint_velocity_limit` | kinematics | A commanded joint rate or acceleration would exceed its limit | clamp to the permitted rate/acceleration |
| `joint_position_limits` | kinematics | A commanded joint target would exceed joint travel | clamp to the permitted position |
| `workspace` | kinematics | End effector would leave its allowed work volume | CBF constraint via QP (falls back to halt without Jacobian) |
| `keep_out_zone` | kinematics | End effector would enter a spherical keep-out zone | CBF constraint via QP (falls back to halt without Jacobian) |
| `orientation_limit` | kinematics | Tool axis tilt exceeds the configured limit | CBF constraint via QP (falls back to halt without angular Jacobian) |
| `task_joint_speed_limit` | execution | Optional task-local aggregate speed cap for custom workflows | `hold_position` after the configured warning streak |
| `task_workspace_bounds` | execution | Current task phase leaves its local work area | `hold_position` after the configured warning streak |
| `check_gripper_clear` | execution | Gripper is obstructed/closed when clearance is required | `hold_position` |
| `task_gripper_command_guard` | execution | Open/close command is invalid in the active task phase or zone | suppress only the gripper command |
| `hardware_watchdog` | hardware | Sensor/robot heartbeat is stale or lost | `emergency_stop` |
| `temperature_limit` | hardware | Motor temperature remains too high | `slow_down` to allow cooling, then escalate if configured |
| `current_limit` | hardware | Sustained over-current suggests collision or stall | `hold_position` |
| `voltage_limit` | hardware | Supply leaves the safe 12 V operating band | `emergency_stop` |
| `force_limit` | hardware | Force sensor detects excessive contact force | `hold_position` |
| `check_force_torque_safe` | hardware | Force/torque sensor detects excessive contact load | `hold_position` |
| `host_health_limit` | host | Controller CPU/GPU/memory/temperature is overloaded | `slow_down` while system health recovers |

### Multi-cycle reaction thresholds

L2 and L3 nodes use the shared structural field `warn_frames` to require
consecutive violating cycles before escalation. A healthy cycle resets the
streak. Use this for transient sensor spikes or momentary task deviations;
do not use `timeout_sec` as a debounce value. `timeout_sec` is only for an
explicitly timed L2 workflow phase, not for cycle-by-cycle L2 limits or L3
hardware health monitoring.

The SO-101 preset exposes only `task_gripper_command_guard` at L2 because its
pick/place phase has a concrete actuator command to validate. The
`task_joint_speed_limit`, `task_workspace_bounds`, and `check_gripper_clear`
callbacks remain available for hand-authored workflows, but are intentionally
not enabled by default: joint speed is already enforced by L1, and the other
two require a task-specific operating region or meaningful gripper telemetry.

```yaml
boundaries:
  voltage_limit:
    layer: L3
    type: single
    nodes:
      - callback: voltage_limit
        warn_frames: 3
        fallback: emergency_stop
        params:
          min_voltage_v: 10.0
          max_voltage_v: 13.0
```

---

## L0: Perception

### `ood_detector`

Unified out-of-distribution boundary callback. Choose the detector through
`backend`; the console and new Stackfiles use `backend: normalizing_flow`
(Real-NVP) by default.

| Param | Default | Description |
|---|---|---|
| `ood_model_path` | `""` | Path to the OOD model |
| `bank_path` | `""` | Path to the memory bank |
| `nn_threshold` | `2.0` | Nearest-neighbour threshold |
| `z_threshold` | `5.0` | Online Welford z-score threshold |
| `nll_sigma` | `3.0` | Real-NVP threshold multiplier (`mean + sigma * std`) |
| `nll_threshold` | `5.0` | NLL threshold (set via EER calibration) |
| `backend` | `"normalizing_flow"` | `normalizing_flow` (Real-NVP), `memory_bank`, or `welford` |
| `device` | `"cpu"` | Inference device |
| `temporal_smoothing_frames` | `3` | Consecutive abnormal frames required before REJECT |
| `vision_model` | `""` | HuggingFace vision backbone (`mobilenet_v3_small`, `mobilenet_v3_large`) |
| `vision_weight` | `0.3` | Weight of vision features in fused embedding (0.0–1.0) |
| `vision_camera` | `""` | Camera key to score; set this to match RQ1 (for example `top`) |

When `vision_model` is set, the callback fuses joint embeddings (128-dim) with vision
embeddings (128-dim, truncated from the backbone output) into a 256-dim vector weighted
by `vision_weight`, using `vision_camera` when configured. This enables cross-scene OOD detection while preserving joint-level
sensitivity. Use `scripts/run_l0_calibration.py` with `--vision-model` to determine
the optimal `nll_threshold` (τ*) via Equal Error Rate calibration.

To use an EER-calibrated threshold, set `nll_sigma: 0` and `nll_threshold: <τ*>` in
the stackfile. When `nll_sigma` is 0, the σ-based heuristic is bypassed and
`nll_threshold` is used directly as the decision boundary.

---

## L1: Physical Kinematics

### `joint_position_limits`

Return False if any joint position violates upper/lower limits.

| Param | Default | Description |
|---|---|---|
| `upper` | SO-101 defaults | Per-joint upper limits (rad) |
| `lower` | SO-101 defaults | Per-joint lower limits (rad) |
| `use_degrees` | `False` | Interpret limits as degrees |

### `joint_velocity_limit`

Per-joint velocity cap — motor safety ceiling.

| Param | Default | Description |
|---|---|---|
| `max_velocities` | `[1.5]*6` | Per-joint max velocity (rad/s) |
| `use_degrees` | `False` | Interpret velocity limits as degrees |

### `joint_acceleration_limit`

Per-joint acceleration cap — smoothing and jitter suppression.
Tracks the previous cycle's velocity; first cycle always passes.

| Param | Default | Description |
|---|---|---|
| `max_acceleration` | — | Per-joint max acceleration (rad/s²) |
| `use_degrees` | `False` | Interpret acceleration limits as deg/s² |

### `ee_velocity_limit`

Clamps end-effector linear speed — environment and human safety.
Uses the linear Jacobian to map joint velocities to EE velocity;
uniformly scales joint velocities when EE speed exceeds the limit.
Passes when no Jacobian is available (requires URDF or dynamics).

| Param | Default | Description |
|---|---|---|
| `max_ee_velocity` | `0.5` | Maximum EE linear speed (m/s) |

### `workspace`

Check if end-effector is within workspace box bounds.

| Param | Default | Description |
|---|---|---|
| `bounds` | `[[-0.4,0.4],[-0.4,0.4],[0.02,0.6]]` | [x,y,z] min/max (m) |

---

## L2: Task Execution

### `check_gripper_clear`

Reject if `obs.metadata["gripper_pos"]` < `min_gripper_opening_m`.

### `task_gripper_command_guard`

Clamp gripper commands that are incompatible with the active task node. The
container/list defines only the phase order; each node defines its own gripper
rule in `params`. The clamp preserves the arm target and suppresses the injected
gripper command by setting `gripper_action` to `None`.

Default pick-and-place stacks use a left-to-right sequence: a 15 cm cube on the
left for `close`, a transfer node that allows no gripper command, and a 15 cm
cube on the right for `open`. The default cube centers are 20 cm apart along X.

```yaml
boundaries:
  task_gripper_sequence:
    layer: L2
    type: list
    nodes:
      - callback: task_gripper_command_guard
        fallback: hold_position
        params:
          allowed_command: close
          zone: [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
      - callback: task_gripper_command_guard
        fallback: hold_position
        params:
          allowed_command: none
      - callback: task_gripper_command_guard
        fallback: hold_position
        params:
          allowed_command: open
          zone: [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]
```

| Param | Default | Description |
|---|---|---|
| `allowed_command` | inferred from action | `close`, `open`, or `none` for the active node |
| `zone` | `None` | Axis-aligned box `[[xmin,xmax],[ymin,ymax],[zmin,zmax]]` where the allowed command may run |
| `pick_zone` | `None` | Legacy alias used when `allowed_command` is omitted and the command is `close` |
| `place_zone` | `None` | Legacy alias used when `allowed_command` is omitted and the command is `open` |
| `close_threshold` | `0.25` | `action.gripper_action <= threshold` means close |
| `open_threshold` | `0.75` | `action.gripper_action >= threshold` means open |

## L3: Hardware Monitoring

### `hardware_watchdog`

Reject if observation is stale (age > `max_staleness_ms`).

### `temperature_limit`

Reject if any motor temperature exceeds threshold.
Reads from the `temperature` observation channel.

| Param | Default | Description |
|---|---|---|
| `max_temperature_c` | `55.0` | Max temperature (°C) |
| `channel` | `"temperature"` | Observation channel name |

### `current_limit`

Reject if any motor current exceeds threshold.
Reads from the `current` observation channel.

| Param | Default | Description |
|---|---|---|
| `max_current_a` | `1.5` | Max current (A) |
| `channel` | `"current"` | Observation channel name |

### `voltage_limit`

Reject if supply voltage is outside safe band.
Reads from the `voltage` observation channel.

| Param | Default | Description |
|---|---|---|
| `min_voltage_v` | `10.0` | Min safe voltage for a nominal 12 V supply (V) |
| `max_voltage_v` | `13.0` | Max safe voltage for a nominal 12 V supply (V) |
| `channel` | `"voltage"` | Observation channel name |

### `force_limit`

Reject if force magnitude from a force/torque observation channel exceeds limit.

| Param | Default | Description |
|---|---|---|
| `max_force_n` | `50.0` | Max force magnitude (N) |
| `channel` | `"force_torque"` | Observation channel name |

### `check_force_torque_safe`

Reject on contact-force / torque overload, read from the typed
`obs.force_torque` field. (Reclassified from L2 → L3: a physical
contact-force limit, the same family as `force_limit`, not task semantics.)

| Param | Default | Description |
|---|---|---|
| `max_force_n` | `50.0` | Max force magnitude (N) |
| `max_torque_nm` | `10.0` | Max torque magnitude (N·m) |

---

## Writing a custom callback

A callback is any callable with signature `(*, obs: Observation, **kwargs) -> bool`
(or `tuple[bool, str]` to attach a reason).

**Built-in style (recommended):** add a `@boundary_callback` function to the
layer module that matches its semantics. The decorator registers it and
records catalog metadata; `register_all()` picks it up automatically.

```python
# dam/boundary/callbacks/kinematics.py
from dam.boundary.callbacks._registry import boundary_callback
from dam.types.observation import Observation

@boundary_callback(name="check_above_table", layer="L1",
                    description="Rejects if the EE drops below the table.")
def check_above_table(*, obs: Observation, table_z: float = 0.05) -> bool:
    if obs.end_effector_pose is None:
        return True
    return float(obs.end_effector_pose[2]) >= table_z
```

**Out-of-tree / runtime style:** register any callable directly.

```python
from dam.registry.callback import get_global_registry
get_global_registry().register("check_above_table", check_above_table)
```

Return `True` → safe, `False` → REJECT.

Callbacks receive their params from the stackfile `params:` block.
The merge-policy registry (`dam/runtime/merge_policy.py`) controls how
multiple boundaries declaring the same param combine values.
