# Library API

DAM can be embedded as a Python library. The public surface lives in `import dam`:

```python
import dam

# Runtime
dam.run(stack, *, task, cycles, ros2_node)  -> RunSummary
dam.build_runner(stack, *, ros2_node)       -> Runner

# Safety guard (no hardware loop needed)
dam.safe(action, obs, stackfile, *, ...)    -> ndarray | dict
dam.SafetyGuard(stackfile, *, task, ...)    -> callable guard
dam.SafetyProcessorStep(stackfile, *, ...)  -> LeRobot processor step

# Registration decorators
@dam.callback(name)                         # register a boundary callback
@dam.guard(layer, *, phase, always)         # register a Guard subclass
@dam.fallback(name, *, monitors_hardware)   # register a fallback Context

# Types
dam.RunSummary      # frozen: .status (str), .cycles (int), .emergency (bool)
dam.Runner          # runner ABC (connect/verify/start/stop/shutdown)
dam.RunnerStatus    # IDLE / STARTING / RUNNING / PAUSED / STOPPING / STOPPED / EMERGENCY
dam.GuardResult     # per-guard evaluation outcome
dam.GuardDecision   # PASS / CLAMP / REJECT
dam.Observation     # sensor state snapshot
dam.ActionProposal  # proposed action from policy
dam.ValidatedAction # action after guard processing
dam.RiskLevel       # NORMAL / ELEVATED / CRITICAL / EMERGENCY
dam.CycleResult     # full cycle telemetry record
```

---

## Managed Loop

Full lifecycle in one call — build, connect, verify, start, wait, shutdown:

```python
import dam

summary = dam.run("demo.yaml", task="pick_place", cycles=200)
print(summary.status, summary.cycles)
```

`cycles=-1` runs unbounded until stopped or faulted. `KeyboardInterrupt` triggers a clean shutdown.

## Manual Control

Drive the lifecycle yourself when you need custom loop logic:

```python
runner = dam.build_runner("demo.yaml")
runner.connect()
runner.verify()
runner.start(task="pick_place", n_cycles=50)

try:
    while runner.status not in (dam.RunnerStatus.STOPPED, dam.RunnerStatus.EMERGENCY):
        pass  # observe runner.cycle_count / runner.status
finally:
    runner.shutdown()
```

---

## Safety Guard API

For validating actions without running a full hardware loop — during recording, offline evaluation, or testing.

### `dam.safe()` — one-liner

```python
safe_action = dam.safe(action, obs, stackfile="safety.yaml")
```

Creates a `SafetyGuard` internally. Convenient but re-initializes every call — use `SafetyGuard` directly for repeated calls.

### `dam.SafetyGuard` — stateful

```python
guard = dam.SafetyGuard("safety.yaml", task="record")

for action, obs in teleop_stream:
    safe_action = guard(action, obs)   # dict→dict or ndarray→ndarray

    # Inspect what happened
    for r in guard.last_results:
        print(r.decision, r.guard_name, r.reason)
```

- Auto-detects `joint_names` and `degrees_mode` from the stackfile's `hardware.preset`
- Rejected actions return hold-position (current joint positions) so loops never break
- Access the underlying runtime via `guard.runtime`

### `dam.SafetyProcessorStep` — LeRobot integration

```python
from dam import SafetyProcessorStep

robot_action_processor.steps.insert(0, SafetyProcessorStep("safety.yaml"))
```

Drop-in `RobotActionProcessorStep` subclass. Lazy init — the guard is created on the first call, not at import time. Falls back to a no-op if LeRobot is not installed.

---

## Registration Decorators

Extend DAM by registering custom callbacks, guards, or fallbacks.

### `@dam.callback(name)`

Register a boundary callback function:

```python
@dam.callback("my_check")
def my_check(*, obs, action, my_param=1.0):
    if obs.joint_positions[0] > my_param:
        return CallbackResult.violate("my_check", "exceeded limit")
    return CallbackResult.ok("my_check")
```

Then reference `callback: my_check` in your Stackfile.

### `@dam.guard(layer)`

Register a Guard subclass:

```python
@dam.guard("L2", phase=1)
class MyTaskGuard(Guard):
    def check(self, obs, action, **kwargs):
        ...
```

### `@dam.fallback(name)`

Register a fallback Context:

```python
@dam.fallback("my_recovery", monitors_hardware=True)
class MyRecovery(StepContext):
    ...
```

Then reference `fallback: my_recovery` on boundary nodes.

---

## CLI

The `dam run` CLI subcommand is a thin shell over `dam.run()` — same behaviour, exit 1 on `EMERGENCY`.
