# Guards Reference

DAM provides four built-in guard layers. Guards are evaluated every cycle in layer order (L0 → L4).

---

## Guard Layers

| Layer | Name | Class | Default |
|-------|------|-------|---------|
| L0 | OOD Detection | `OODGuard` | Enabled if configured |
| L2 | Motion Safety | `MotionGuard` | Enabled via Stackfile |
| L3 | Execution | `ExecutionGuard` | Enabled when boundaries active |
| L4 | Hardware | `HardwareGuard` | Enabled when sink provides status |

---

## OODGuard (L0)

Rejects observations that appear out-of-distribution.

**Detector priority:**

1. **Memory Bank** (when trained) — extracts a 128-dim L2-normalised feature vector,
   queries the nearest-neighbour distance in the memory bank.
   Rejects if `dist > nn_threshold`.

2. **Welford z-score** (fallback) — online mean/variance estimator.
   Rejects if `max_z > z_threshold`. 30-sample warm-up period.

**Training the memory bank:**

```python
from dam.guard.builtin.ood import OODGuard

guard = OODGuard()
guard.train(normal_observations)   # list[Observation]
guard.save("extractor.pt", "bank.npy")
```

**Config pool keys:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `nn_threshold` | float | 0.5 | NN distance threshold |
| `ood_model_path` | str | None | Path to feature extractor weights |
| `bank_path` | str | None | Path to memory bank `.npy` file |

---

## MotionGuard (L2)

Enforces joint limits, velocity limits, workspace bounds, and acceleration limits.

**Decision logic:**

1. Workspace bounds → **REJECT** if end-effector outside bounds
2. Joint limits → **CLAMP** positions to `[lower_limits, upper_limits]`
3. Velocity limits → **CLAMP** velocity vector to `max_velocity`
4. Acceleration limits → **CLAMP** velocity to respect `max_acceleration`

**Config pool keys:**

| Key | Type | Description |
|-----|------|-------------|
| `upper_limits` | ndarray | Per-joint upper limits [rad] |
| `lower_limits` | ndarray | Per-joint lower limits [rad] |
| `max_velocity` | ndarray | Per-joint max velocity [rad/s] |
| `max_acceleration` | ndarray | Per-joint max acceleration [rad/s²] |
| `bounds` | ndarray (3,2) | XYZ workspace bounds [[min,max], ...] |

---

## ExecutionGuard (L3)

Evaluates active boundary node constraints each cycle.

**Checks (in order):**

1. `max_speed` — joint velocity norm vs node constraint
2. `bounds` — end-effector vs node constraint
3. `max_force_n` — force/torque norm vs constraint
4. `callback` — registered callbacks (see [Boundary Callbacks](boundary-callbacks.md))
5. `timeout_sec` — node active duration vs configured timeout

---

## HardwareGuard (L4)

Monitors hardware telemetry from the sink adapter.

**Checks:**

| Field | Threshold key | Default |
|-------|--------------|---------|
| `temperature_c` | `max_temperature_c` | 70°C |
| `current_a` | `max_current_a` | 5.0 A |
| `error_codes` | any non-zero | — |

Returns **FAULT** on any error code, **REJECT** on limit violation, **PASS** if `hardware_status` is None (graceful degradation).

---

## Custom Guards

Implement the `Guard` ABC and decorate with `@guard("L<n>")`:

```python
import dam
from dam.guard.base import Guard
from dam.types.observation import Observation
from dam.types.result import GuardResult

@dam.guard("L2")
class MyForceGuard(Guard):
    def check(self, obs: Observation, max_force: float = 50.0) -> GuardResult:
        if obs.force_torque is not None:
            if float(np.linalg.norm(obs.force_torque[:3])) > max_force:
                return GuardResult.reject("force limit", self.get_name(), self.get_layer())
        return GuardResult.pass_(self.get_name(), self.get_layer())
```
