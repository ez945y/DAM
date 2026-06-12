# DAM 0.6.0 Release Notes

DAM 0.6.0 makes high-rate control honest: instead of claiming the full RSMF
stack fits a 60 Hz budget, the pipeline now splits into a fast lane that runs
at control rate and an asynchronous slow lane for expensive guards, with an
explicit freshness contract between them. Hardware telemetry reads are
decimated off the hot path, and the kinematics stack aligns observation
joints with the URDF model by name instead of by position.

## Fast/Slow Lane Split

High control rates (60 Hz = 16.67 ms) cannot absorb vision OOD inference,
QP solves, and telemetry reads in a single synchronous pipeline. The new
`safety.slow_lane` section moves expensive guards onto an async evaluator
while keeping one control loop as the single writer to the sink:

```yaml
safety:
  control_frequency_hz: 60
  slow_lane:
    frequency_hz: 10
    max_staleness_ms: 500
    stale_action: reject   # reject (trigger fallback) | warn (log only)
```

- Lane assignment binds to **guards** and defaults by layer: L0/L2 → slow,
  L1/L3 → fast. Override per guard with `lane: fast|slow` in the `guards:`
  section.
- Ordering contracts are preserved across the split: the fast lane runs
  L1 → L3 and publishes its post-L1 (clamped) action to the slow lane, so
  L2 still evaluates what was actually commanded; the slow lane runs
  L0 → L2 in stage order.
- Hand-offs are latest-wins mailboxes — the slow lane scores only the newest
  snapshot, never queues, and never blocks the control loop. The latest slow
  verdict joins every fast cycle's aggregate: a slow-lane REJECT latches
  until a newer verdict clears it and triggers the normal fallback
  machinery; slow-lane CLAMPs escalate to REJECT because a clamp computed
  against an older cycle cannot be applied to the current one.
- Staleness is the synchronisation contract: a verdict older than
  `max_staleness_ms` (worker overloaded or dead) triggers the
  `slow_lane_watchdog` per `stale_action`. There is no shared mutable state
  between lanes beyond the two mailboxes.
- Documented trade-off: L0 OOD reaction latency is bounded by
  `1/frequency_hz + max_staleness_ms` instead of one control cycle.
  Distribution shift is not a millisecond-scale phenomenon; millisecond-scale
  hazards (kinematic limits, hardware faults) stay in the fast lane.

The console exposes the slow lane in Configuration → Safety and a per-guard
lane selector in Guard Routing; both round-trip through the YAML editor.

## Telemetry Decimation

- Channel registers (temperature / current / voltage) ride the same serial
  bus as joint state and cost one `sync_read` each per cycle. New
  `hardware.telemetry_hz` decimates those reads; between bus reads the
  adapter serves a cached snapshot stamped with
  `metadata["telemetry_timestamp"]`.
- The ABI contract for consumers (L3 `HardwareGuard`): hardware status may be
  up to `1/telemetry_hz` old and carries its own timestamp — never assume
  same-cycle freshness. The bus keeps exactly one owner (the control-loop
  read path); telemetry is decimated there rather than read from another
  thread.

## Validation Lifecycle

- `validate()` gains explicit lifecycle flags (`commit_state`,
  `advance_cycle`, `emit_side_effects`) so fallback contexts can run shadow
  validations without advancing cycle state, committing follower-error
  baselines, or double-logging to MCAP.
- The runtime now remembers validated velocities alongside positions, and
  `step()` commits the *final* action of the cycle — including fallback
  post-processing — as the baseline for following-error detection.

## Kinematics Alignment

- The reduced URDF model (e.g. SO-ARM: 5 arm joints, gripper locked) is now
  aligned with the observation vector **by joint name** via the preset joint
  layout, replacing positional truncation that silently assumed modelled
  joints lead the observation. `DynamicsContext.q_indices_for()` provides the
  mapping; the FK pool publishes `jacobian_joint_indices` so callbacks gather
  joint state instead of guessing.
- Fixes an emergency stop caused by `ee_velocity_limit`'s QP linearization
  multiplying a 5-column Jacobian row against a 6-motor joint vector. QP
  constraint rows are now scattered into observation width with explicit zero
  coefficients for unmodelled joints.

## Input Space Controls

- `hardware.input_space` / `policy.input_space` (`joint` | `ee`) declare the
  action space; `ee` mode validates EE poses through IK/FK via the preset
  URDF. The console Configuration page exposes the toggle and warns when the
  selected preset lacks a URDF. Example stackfiles and quick-stack docs now
  document both fields.

## Packaging

- The project is packaged as `robot-dam` with the `dam-rs` Rust extension
  resolved from the local workspace (`dam-rust/dam-py`); `make setup` no
  longer requires extras that moved into core dependencies (torch, lerobot).
- Isaac Sim integration installs separately via pip (no macOS / Python ≥3.11
  wheels exist); the `isaac` extra remains for compatibility.
