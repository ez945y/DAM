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
| `callbacks/perception.py` | L0 | `ood_detector` |
| `callbacks/kinematics.py` | L1 | joint / workspace / Cartesian / keep-out / orientation / geofence |
| `callbacks/execution.py` | L2 | task speed / task workspace / gripper clear / gripper command guard |
| `callbacks/hardware.py` | L3 | watchdog, temperature / current / voltage / force |

Adding a `@boundary_callback` function to the matching module is all that is
needed — `register_all()` discovers it automatically (no list to maintain).

---

## L0: Perception

### `ood_detector`

Out-of-distribution boundary callback — wraps OODGuard.
Return False if the observation is flagged as out-of-distribution.

| Param | Default | Description |
|---|---|---|
| `ood_model_path` | `""` | Path to the OOD model |
| `bank_path` | `""` | Path to the memory bank |
| `nn_threshold` | `2.0` | Nearest-neighbour threshold |
| `nll_threshold` | `5.0` | NLL threshold |
| `backend` | `"memory_bank"` | OOD backend |
| `temporal_smoothing_frames` | `3` | Consecutive abnormal frames required before REJECT |

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

Return False if any joint velocity exceeds limits.

| Param | Default | Description |
|---|---|---|
| `max_velocities` | `[1.5]*6` | Per-joint max velocity (rad/s) |
| `use_degrees` | `False` | Interpret limits as degrees |

### `workspace`

Check if end-effector is within workspace box bounds.

| Param | Default | Description |
|---|---|---|
| `bounds` | `[[-0.4,0.4],[-0.4,0.4],[0.02,0.6]]` | [x,y,z] min/max (m) |

### `check_velocity_smooth`

Reject if joint velocity norm exceeds `max_jerk_norm` per cycle.

### `check_joints_not_moving`

Reject if any joint moves faster than `max_speed_rad_s`.

### `cartesian_velocity_limit`

Reject if the end-effector twist exceeds Cartesian speed limits. The 0.25 m/s
default follows ISO/TS 15066 reduced-speed guidance for human-collaborative
operation. Requires a `dynamics` (pinocchio) context for the frame Jacobian;
passes when unavailable.

| Param | Default | Description |
|---|---|---|
| `max_linear_speed` | `0.25` | Max EE linear speed (m/s) |
| `max_angular_speed` | `1.0` | Max EE angular speed (rad/s) |
| `frame` | `None` | Frame id/name (defaults to the primary EE frame) |

### `keep_out_zone`

Reject if the end-effector position is inside any keep-out region — the
inverse of `workspace`, for fixtures and operator-occupied volumes. Uses the
`dynamics` context or `kinematics_resolver`; passes when neither resolves a
pose.

| Param | Default | Description |
|---|---|---|
| `boxes` | `None` | List of `[[xmin,xmax],[ymin,ymax],[zmin,zmax]]` (m) |
| `spheres` | `None` | List of `[cx,cy,cz,radius]` (m) |

### `orientation_limit`

Reject if the tool axis tilts past `max_tilt_deg` from a reference direction —
keeps a carried payload upright.

| Param | Default | Description |
|---|---|---|
| `max_tilt_deg` | `30.0` | Max tilt of `tool_axis` from `reference_axis` (deg) |
| `reference_axis` | `[0,0,1]` | World direction to align with (default: up) |
| `tool_axis` | `[0,0,1]` | EE-frame axis to keep aligned (default: local +Z) |

### `base_geofence`

Reject if the mobile base leaves an allowed area. Reads the planar `[x, y]`
base pose from `obs.channels[channel]`; passes when the channel is absent
(fixed arms). When both `bounds` and `polygon` are given, the base must
satisfy both.

| Param | Default | Description |
|---|---|---|
| `bounds` | `None` | Axis-aligned box `[[xmin,xmax],[ymin,ymax]]` (m) |
| `polygon` | `None` | List of `[x,y]` vertices (≥3) defining the allowed region |
| `channel` | `"base_pose"` | Observation channel holding the base pose |

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
| `min_voltage_v` | `6.0` | Min safe voltage (V) |
| `max_voltage_v` | `8.5` | Max safe voltage (V) |
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
