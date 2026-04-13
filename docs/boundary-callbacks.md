# Boundary Callbacks

Built-in check functions for `BoundaryConstraint.callback`.
These are ready-to-use callbacks that the `ExecutionGuard` evaluates each cycle.

---

## Quick start

```python
from dam.boundary.builtin_callbacks import register_all
import numpy as np

# Register all built-in callbacks with sensible defaults
register_all(
    upper_soft=np.array([1.9, 1.7, 1.4, 1.7, 1.9, 1.9]),  # optional soft limits
    lower_soft=np.array([-1.9, -1.7, -1.4, -1.7, -1.9, -1.9]),
    ee_min_height_m=0.02,
    max_force_n=50.0,
    max_torque_nm=10.0,
)
```

Then reference by name in your Stackfile:

```yaml
boundaries:
  grasp_zone:
    nodes:
      - node_id: default
        constraint:
          callback:
            - check_force_torque_safe
            - check_joints_not_moving
```

---

## Built-in callbacks

### `check_joint_soft_limits`

Reject if any joint position exceeds soft limits (tighter than hard motion limits).
Gives the robot time to decelerate before `MotionGuard` clamps.

```python
register("check_joint_soft_limits", check_joint_soft_limits,
         upper_soft=np.ones(6), lower_soft=-np.ones(6))
```

```python
register_all(ee_min_height_m=0.05)
```

### `check_velocity_smooth`

Reject if joint velocity norm exceeds `max_jerk_norm` per cycle.

### `check_force_torque_safe`

Reject if force magnitude > `max_force_n` or torque magnitude > `max_torque_nm`.

### `check_joints_not_moving`

Reject if any joint moves faster than `max_speed_rad_s`. Use on stationary nodes
(e.g. tool-change, handover).

### `check_gripper_clear`

Reject if `obs.metadata["gripper_pos"]` is below `min_gripper_opening_m`.

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
