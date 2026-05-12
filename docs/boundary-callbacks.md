# Boundary Callbacks

Built-in check functions for `BoundaryConstraint.callback`.
These are ready-to-use callbacks that the `ExecutionGuard` evaluates each cycle.

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
        fallback: emergency_stop
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

---

## L2: Task Execution

### `check_force_torque_safe`

Reject if force magnitude > `max_force_n` or torque > `max_torque_nm`.

### `check_gripper_clear`

Reject if `obs.metadata["gripper_pos"]` < `min_gripper_opening_m`.

### `semantic_state`

High-level semantic task state validation (placeholder).

---

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

---

## Writing a custom callback

A callback is any callable with signature `(*, obs: Observation, **kwargs) -> bool`:

```python
import numpy as np
from dam.types.observation import Observation
from dam.registry.callback import get_global_registry

def check_above_table(*, obs: Observation, table_z: float = 0.05) -> bool:
    if obs.end_effector_pose is None:
        return True
    return float(obs.end_effector_pose[2]) >= table_z

# Register
get_global_registry().register("check_above_table", check_above_table)
```

Return `True` → safe, `False` → REJECT.

Callbacks receive their params from the stackfile `params:` block.
The merge-policy registry (`dam/runtime/merge_policy.py`) controls how
multiple boundaries declaring the same param combine values.
