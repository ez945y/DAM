# Guards Reference

DAM provides four built-in guard layers. Guards are evaluated every cycle in layer order (L0 → L3).

---

## Guard Layers

| Layer | Name | Class | Default |
|-------|------|-------|---------|
| L0 | OOD Detection | `OODGuard` | Enabled if configured |
| L1 | Physical Kinematics | `MotionGuard` | Enabled via Stackfile |
| L2 | Task Execution | `ExecutionGuard` | Enabled when boundaries active |
| L3 | Hardware Monitoring | `HardwareGuard` | Enabled when sink provides status |

---

## OODGuard (L0)

Rejects observations that appear out-of-distribution.

**Detector priority:**

1. **Memory Bank** (when trained) — extracts a 128-dim L2-normalised feature vector,
   queries the nearest-neighbour distance in the memory bank.
   Rejects if `dist > nn_threshold`.

2. **Welford z-score** (fallback) — online mean/variance estimator.
   Rejects if `max_z > z_threshold`. 30-sample warm-up period.

**Training the density model:**

Training fits a density model over normal observations and saves two statistics
(`mean_train_nll`, `std_train_nll`) alongside the model weights.

**Threshold calibration (recommended):**

Use `scripts/run_l0_calibration.py` to compute the optimal threshold via
Equal Error Rate (EER) on a held-out calibration set. The script outputs τ*
which should be set as `nll_threshold` in the stackfile. This replaces the
legacy σ-based heuristic with a data-driven operating point.

```python
from dam.guard.builtin.ood import OODGuard

guard = OODGuard()
guard.train(normal_observations)   # list[Observation]
guard.save("extractor.pt", "bank.npy")
```

**Vision feature fusion (optional):**

When `vision_model` is configured, the guard fuses joint embeddings (128-dim)
with pretrained vision features (MobileNetV3) into a 256-dim vector. This enables
cross-scene OOD detection. The `vision_weight` parameter controls the balance
(0.0 = joint only, 1.0 = vision only; default 0.3). Set `vision_camera` to
the same camera used during RQ1 calibration.

**Config pool keys:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `nn_threshold` | float | 0.5 | NN distance threshold (memory-bank detector) |
| `nll_threshold` | float | 5.0 | Direct NLL threshold (set via EER calibration). Used when `nll_sigma` ≤ 0. |
| `nll_sigma` | float | 3.0 | σ multiplier. Set to 0 to use `nll_threshold` directly (EER mode). |
| `temporal_smoothing_frames` | int | 3 | Consecutive abnormal observations required before an OOD REJECT is surfaced through the boundary callback. Use 1 to disable smoothing. |
| `ood_model_path` | str | None | Path to feature extractor weights |
| `bank_path` | str | None | Path to memory bank `.npy` file |
| `vision_model` | str | None | HuggingFace vision backbone (e.g. `mobilenet_v3_small`) |
| `vision_weight` | float | 0.3 | Vision feature weight in fused embedding (0.0–1.0) |
| `vision_camera` | str | None | Camera image key used for vision features (for example `top`) |

---

## MotionGuard (L1)

Enforces joint limits, velocity limits, workspace bounds, and acceleration limits.

**Decision logic:**

1. Workspace bounds → **REJECT** if end-effector outside bounds
2. Joint limits → **CLAMP** positions to configured `lower` and `upper` values
3. Velocity limits → **CLAMP** velocity vector to `max_velocities` (motor safety)
4. Acceleration limits → **CLAMP** velocity change to `max_acceleration` (smoothing)

**Common Stackfile callback params:**

| Key | Type | Description |
|-----|------|-------------|
| `upper` | list[float] | Per-joint upper limits [rad] for `joint_position_limits` |
| `lower` | list[float] | Per-joint lower limits [rad] for `joint_position_limits` |
| `max_velocities` | list[float] | Per-joint max velocity [rad/s] for `joint_velocity_limit` |
| `max_acceleration` | list[float] | Per-joint max acceleration [rad/s²] for `joint_acceleration_limit` |
| `bounds` | list[list[float]] | XYZ workspace bounds [[min,max], ...] for `workspace` |

---

## ExecutionGuard (L2)

Evaluates active boundary node constraints each cycle.

**Checks (in order):**

1. `callback` — registered L2 callback (see [Boundary Callbacks](boundary-callbacks.md))
2. `timeout_sec` — node active duration vs configured timeout

Built-in L2 callbacks cover task speed, task workspace bounds, gripper
clearance, and active-node gripper command validation. Incompatible gripper
commands are clamped to no-op rather than rejecting the whole action. The
default pick-and-place stack uses a left pick cube and right place cube; the
list container only orders those nodes, while each node keeps its own
`allowed_command` and `zone` params.

L2 guard output is recorded on `/dam/L2` with `event_class: "task"`. The same
per-boundary `GuardResult` list feeds cycle aggregation, MCAP, the console, and
replay.

## Fallback Contexts

Fallbacks are `StepContext` classes registered with `@dam.fallback`. Stackfile
nodes reference them by fallback name; named entries can add `params`,
`escalate_to`, and `escalate_after_seconds`.

Built-ins: `emergency_stop`, `hold_position`, `wait_and_retry`, `slow_down`,
`retreat`. Contexts have `requires_proposal` and `monitors_hardware` flags;
hardware monitoring stays on during fallback unless the Context disables it
(`emergency_stop` does).

---

## HardwareGuard (L3)

Monitors hardware telemetry from the sink adapter.

**Checks:**

| Field | Threshold key | Default |
|-------|--------------|---------|
| `temperature_c` | `max_temperature_c` | 70°C |
| `current_a` | `max_current_a` | 5.0 A |
| `error_codes` | any non-zero | — |
| `following_error_rad` | `max_following_error_rad` | 0.1 rad |

Returns **FAULT** on any error code, **REJECT** on limit violation, **PASS** if `hardware_status` is None (graceful degradation).

**Injection keys:**

| Key | Type | Description |
|-----|------|-------------|
| `prev_validated_positions` | ndarray | Last validated joint positions used to compute per-joint following error |
| `max_following_error_rad` | float | Per-joint following error limit [rad]. Defaults to 0.1 rad. |

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
