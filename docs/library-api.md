# Library API

DAM can be embedded as a Python library. The public surface lives in `import dam`:

```python
import dam

# Runtime
dam.run(stack, *, task, cycles, ros2_node)  -> RunSummary
dam.build_runner(stack, *, ros2_node)       -> Runner

# Guardrail (no hardware loop needed) — dict in, validated command out
dam.guardrail(inputs, stackfile, *, ...)        -> ndarray | dict
dam.Guardrail(stackfile, *, task, safe_action)  -> callable guard
dam.GuardrailProcessorStep(stackfile, *, ...)   -> LeRobot processor step

# Registration decorators
dam.register_callback(name, fn=None, *, layer, category, description, params)
dam.register_preset(name, *, joint_names, asset, solvers, action_layout)
dam.register_solver(name, solver, *, capabilities)
dam.register_read_interface(type, factory)
dam.register_write_interface(type, factory)
@dam.callback(name, *, layer)               # register a boundary callback
@dam.guard(layer, *, phase, always)         # register a Guard subclass
@dam.solver_factory(name, *, capabilities)  # register a config-driven solver
@dam.fallback(name, *, monitors_hardware)   # register a fallback Context

# Types
dam.RunSummary      # frozen: .status (str), .cycles (int), .emergency (bool)
dam.Runner          # runner ABC (connect/verify/start/stop/shutdown)
dam.RunnerStatus    # IDLE / STARTING / RUNNING / PAUSED / STOPPING / STOPPED / EMERGENCY
dam.SensorAdapter   # optional base class for read interfaces
dam.ActionAdapter   # optional base class for write interfaces
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

## Guardrail API

Validate a command without running a full hardware loop — during recording,
offline evaluation, or testing. **One dict in, one validated command out.**

The mental model is one sentence: *whatever key you put in the dict, a callback
can receive by declaring a parameter of the same name.* `action` is the reserved
key — the command to validate; every other key is an observation group. Standard
keys are also folded into the `obs` object for builtin guards (`joints` /
`<joint>.pos` → joint positions, `images` / camera frames, `current` /
`temperature` / `voltage` and any other array → `obs.channels`).

### `dam.Guardrail` — stateful

```python
rail = dam.Guardrail("jetbot.yaml", safe_action=[0.0, 0.0])
# Prints its contract on construction:
#   [Guardrail] jetbot.yaml · task=default
#     requires obs: base_pose
#     action:       [v, omega]  (command space)
#     on reject:    [0. 0.]

safe_cmd = rail({"base_pose": [x, y, yaw], "action": [v, omega]})

for r in rail.last_results:           # inspect what happened
    print(r.decision, r.guard_name, r.reason)
```

A callback declares the observation groups it needs by name — the runtime
injects them, no positional slicing:

```python
@dam.callback("forward_only", layer="L1")
def forward_only(*, base_pose, action, solvers, dt=0.1):
    x_next, *_ = solvers["ackermann"].rollout(base_pose, action, dt)
    return bool(x_next >= 0.0)
```

- Auto-detects `joint_names` and `degrees_mode` from `hardware` / the preset.
- The input dict is checked against the contract: a missing required obs group,
  or one whose key collides with a reserved runtime key, raises immediately.
- Actions and explicit fallback vectors must have exactly the configured command
  length and contain only finite values. Joint observations must be complete;
  missing joint positions are not filled with zeros. Action dictionaries require
  a fully keyed `action_layout` and every configured key; partial dictionaries
  raise `ValueError` instead of being passed through.
- `safe_action` on reject: an explicit vector (e.g. `[0, 0]` to stop a base),
  `"hold"` (default — re-issue the current joint positions, safe for a
  position-controlled arm), or `"zero"`.
- The return value mirrors the `action` you passed (list/ndarray/tensor, or a
  `{key: value}` dict). Pass `quiet=True` to suppress the contract print.

Use a context manager for a session so background workers and recording resources
are released even if your control loop raises:

```python
with dam.Guardrail("jetbot.yaml", safe_action=[0.0, 0.0]) as rail:
    safe_cmd = rail({"base_pose": [x, y, yaw], "action": [v, omega]})
    results = rail.last_results
```

`close()` is idempotent. A closed instance cannot validate more actions; create a
new instance for the next session. The one-shot `dam.guardrail()` helper closes
its instance on success or failure. `GuardrailProcessorStep.reset()` and
`close()` release the current session; the next action starts a new one lazily.
A processor transition without an observation raises `ValueError`.

Floating-point PyTorch inputs are detached and converted for validation; the
returned action preserves the input tensor's dtype and device, without autograd
history. Integer action tensors are rejected to prevent truncating corrections.
`set_ee_pose()` requires exactly seven finite values and stores a copy.

### `dam.guardrail()` — one-liner

```python
safe_cmd = dam.guardrail({"base_pose": pose, "action": cmd}, "jetbot.yaml", safe_action=[0, 0])
```

Builds a `Guardrail` internally. Convenient but re-initializes every call — use
`Guardrail` directly for repeated calls.

### Slow-lane readiness and waiting

Without `safety.slow_lane`, all configured checks run synchronously. With a
slow lane, the first call now waits for its own slow verdict by default:

```yaml
safety:
  slow_lane:
    task_hz: 10
    first_verdict: wait       # wait (default) or reject without waiting
    verdict_timeout_ms: 500   # finite, positive deadline for each wait
    max_staleness_ms: 500     # includes snapshot queue and evaluation time
    stale_action: reject     # reject (default) or warn for subsequent cached verdicts
```

In ENFORCE mode, no first verdict means rejection, including when
`stale_action: warn`. With `first_verdict: reject`, a session returns its
`safe_action` while the background worker is still pending. With `wait`, a
timeout returns `safe_action` and leaves a `slow_lane_readiness` REJECT in
`last_results`, with `metadata["slow_lane_state"] == "timeout"`. Pending
nonblocking calls use `"pending"`. A completed REJECT or FAULT also selects
`safe_action`. Slow CLAMP results remain conservatively converted to REJECT.

Later session calls normally use the latest slow verdict, which may describe an
older command. Use `Guardrail(..., wait_for_slow=True)` to wait for each command's
own verdict. `dam.guardrail()` always uses this behavior, even if the Stackfile
specifies `first_verdict: reject`. A waited verdict must also satisfy
`max_staleness_ms`, regardless of `stale_action`.

```python
with dam.Guardrail("safety.yaml", wait_for_slow=True) as rail:
    command = rail({"joints": measured_joints, "action": proposed_joints})
    print(rail.slow_lane_status)
    for result in rail.last_results:
        print(result.decision.name, result.reason, result.metadata)
```

`slow_lane_status` (also on `GuardRuntime`) contains `state`, `basis_cycle_id`,
and `age_ms`. States are `disabled`, `pending`, `ready`, `stale`, and `bypassed`
(LOG_ONLY). **Ready means evidence is available, not that the command passed.**
Completed slow results include `basis_cycle_id`, `verdict_age_ms`, and
`current_cycle_checked`; the last field distinguishes this command's evidence
from a cached result. Status is a live snapshot and can change after a call;
`last_results` records what that call used. Task restart resets readiness.

MONITOR still returns the original command while recording rejection/fault;
LOG_ONLY skips checks. Neither mode claims enforced safety. The wait deadline
covers slow scheduling and evaluation, not construction, fast checks, or
cleanup. Python cannot cancel a running callback: `close()` may additionally
wait up to the worker stop timeout (currently two seconds) and raises
`TimeoutError` if it is still running. A session can retry close after the
callback exits; a one-shot call propagates that cleanup error.

### Action segments & solvers

When the action is a structured command (an EE pose, a base twist), declare its
shape in `hardware.action_layout`. Each segment lists its `keys` (or a typed
size like `ee_pose`), and the per-segment vectors are exposed to callbacks via
`action.metadata["action_segments"]`; the chosen `solver` does the embodiment
math.

```yaml
hardware:
  action_layout:
    - name: arm
      keys: [x, y, z, yaw, pitch, roll]   # 6-DoF EE pose; size == len(keys)
      solver: arm_kinematics
    - name: gripper
      keys: [gripper]
```

### `dam.GuardrailProcessorStep` — LeRobot integration

```python
from dam import GuardrailProcessorStep

robot_action_processor.steps.insert(0, GuardrailProcessorStep("safety.yaml"))
```

Drop-in `RobotActionProcessorStep` subclass. Lazy init — the guard is created on the first call, not at import time. Falls back to a no-op if LeRobot is not installed.

---

## Registration Decorators

Extend DAM by registering custom callbacks, guards, solvers, or runtime
read/write interfaces. Presets are YAML-managed in `assets/presets.yaml`:

```yaml
presets:
  my_arm:
    joint_names: [shoulder, elbow, wrist]
    asset:
      type: urdf
      path: /opt/robots/my_arm.urdf
    solvers:
      pinocchio_kinematics:        # key IS the registered solver name / type
        capabilities: [fk, ik]
    action_layout:
      - name: arm
        keys: [x, y, z, yaw, pitch, roll]
        solver: pinocchio_kinematics
```

### `dam.register_callback(...)`

Register a boundary callback from application code. It is added to both the
runtime callback registry and the callback catalog used by tools:

```python
import dam

@dam.register_callback(
    "my_workspace_rule",
    layer="L2",
    category="execution",
    description="Rejects actions outside the app-defined workspace",
    params={"max_x": "Maximum allowed x coordinate"},
)
def my_workspace_rule(*, obs, action, max_x=0.5):
    return obs.ee_pos[0] <= max_x
```

Or register an existing function directly:

```python
dam.register_callback("my_check", my_check, layer="L1")
```

Then reference `callback: my_workspace_rule` in your Stackfile.

### `dam.register_solver(...)`

Register a live solver object:

```python
dam.register_solver(
    "isaac_arm",
    IsaacUsdSolver(articulation),
    capabilities=["kinematics", "collision"],
)
```

Pass it into `Guardrail` by name or object map:

```python
guard = dam.Guardrail("safety.yaml", solvers={"arm_kinematics": isaac_solver})
```

### `@dam.solver_factory(...)`

Register a Stackfile-instantiable solver factory. The name you register under
IS the name stackfiles reference (the solvers-block key) — there is no separate
`type`. The factory **declares the params it needs as keyword arguments**; DAM
injects matching values from the stackfile `params` plus robot context it knows
(`asset_path`, `asset_type`, `asset`, `observation_joint_names`). Params the
factory does not declare are dropped — no `params.get()`, and you never receive
keys you did not ask for. Declare `**kwargs` to receive everything.

```python
@dam.solver_factory("isaac_usd", capabilities=["fk", "ik"])
def make_usd_solver(prim_path, observation_joint_names=None):
    return IsaacUsdSolver(prim_path, joints=observation_joint_names)
```

Stackfile — the solver key is the registered name:

```yaml
hardware:
  preset: my_arm
  solvers:
    isaac_usd:                 # key == registered name
      params:
        prim_path: /World/Robot
```

### `dam.register_read_interface(...)` / `dam.register_write_interface(...)`

Register runtime IO implementations for custom `hardware.interfaces` types.
Presets and solvers still describe robot/action
semantics; interfaces only read observations or write validated commands.

```python
import time

import dam
from dam.types.observation import Observation

class MyRobot:
    def read(self):
        return Observation(timestamp=time.monotonic(), joint_positions=[0, 0, 0])

    def apply(self, action):
        ...

def make_reader(name, cfg, context):
    return MyRobot()

dam.register_read_interface("my_robot", make_reader)
```

Stackfile:

```yaml
hardware:
  preset: my_arm
  interfaces:
    arm:
      type: my_robot
      capabilities: [observe_joints, command_joints]
      endpoint: /dev/my-robot
```

If read and write are different objects, register a write factory and set
`command_joints` on a separate interface:

```python
dam.register_write_interface("my_robot_command", make_writer)
```

```yaml
hardware:
  interfaces:
    arm_state:
      type: my_robot_state
      capabilities: [observe_joints]
    command:
      type: my_robot_command
      capabilities: [command_joints]
      ref: arm_state
      endpoint: /dev/my-command
```

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
