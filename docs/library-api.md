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
rail.evaluate(inputs)                         -> GuardrailEvaluation
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
dam.GuardDecision   # PASS / CLAMP / REJECT / FAULT
dam.GuardrailEvaluation  # returned command + captured decision evidence
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

- Resolves `joint_names` from `hardware` / the preset. `degrees_mode` comes from
  the motor interface's `source.degrees_mode`, then `hardware.degrees_mode`,
  and defaults to `True`; presets do not declare units. Explicit constructor
  arguments override these settings.
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
  Non-joint command layouts require an explicit vector or `"zero"`; the default
  `"hold"` raises instead of inventing an all-zero fallback for an unknown command.
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

For well-formed commands, MONITOR returns the original command while recording
safety rejection/fault; LOG_ONLY skips safety checks. Both still enforce command
payload validity. Neither mode claims enforced safety. The wait deadline
covers slow scheduling and evaluation, not construction, fast checks, or
cleanup. Python cannot cancel a running callback: `close()` may additionally
wait up to the worker stop timeout (currently two seconds) and raises
`TimeoutError` if it is still running. A session can retry close after the
callback exits; a one-shot call propagates that cleanup error.

### Runtime output and fallback contract

`GuardRuntime.validate()` rejects malformed proposals with an `input_contract`
FAULT in every mode, including MONITOR and LOG_ONLY. Before dispatch, `step()`
checks every final action, including custom contexts and fallback outputs:
nonempty finite 1-D positions, the declared command width, matching finite
optional velocities, finite nonnegative timestamp, and optional gripper in [0, 1].
Command layout takes priority over observation joint count. With no declared
width, the final command must match the proposal when one exists; direct runtimes
without a proposal or declared width can only check intrinsic payload validity.

`CycleResult.output_status` is `sent` only after the sink method returns normally;
this is transport acceptance, not physical motion feedback. Other values are
`suppressed`, `failed`, `not_configured`, and the default `not_attempted` for
externally constructed results. A sink exception clears the returned validated
action and records a sink FAULT; command history is committed only after accepted
dispatch. Policy, context lifecycle, fallback and final-output failures are
reported in cycle guard results. ENFORCE enters a terminal emergency-stop context
on execution faults; MONITOR/LOG_ONLY suppress malformed output without adding
safety fallback actuation. Failure of the physical stop hook is recorded and the
runtime remains in a terminal don't-send state; this does not prove hardware stopped.

On a fallback transition, entry hooks run immediately, while the new context's
step begins next cycle. The transition cycle sends no command, avoiding duplicate
callback evaluation. Hold without a prior valid command remains suppressed;
Retreat requires a prior accepted position and explicit finite `target_positions`
of the same shape. Missing or invalid prerequisites produce a fallback FAULT and
terminal suppression; no implicit zero target or return to normal operation is used.
HoldPosition, Retreat and SlowDown reject non-joint command layouts. SlowDown
suppresses output without a previous accepted position, requires a finite scale
in `[0, 1]`, and preserves the validated timestamp and gripper command.
Payload validation does not establish
that an arbitrary custom fallback is physically safe or satisfies every geometric
constraint; deployment still needs a suitable action and fallback contract.

### Configuration objects and static preflight

`Guardrail` and `guardrail` accept a string/Path filename, a mapping, or a public
`dam.StackfileConfig`. Mapping/model inputs are validated and copied; subsequent
caller mutation does not change the session. File construction keeps the same
behavior. Use `dam.preflight(source, task=..., safe_action=...)` before construction
when you need a static report without allocating runtime resources.

```python
configuration = {
    "hardware": {"joint_names": ["a", "b"], "degrees_mode": False},
    "guards": [],
    "tasks": {"default": {"boundaries": []}},
}
report = dam.preflight(configuration)
report.raise_for_errors()
with dam.Guardrail(configuration, quiet=True) as rail:
    command = rail({"joints": [0, 0], "action": [0.1, 0.2]})
```

This minimal example demonstrates construction, not a safety envelope. The
executable version is `python -m examples.guardrail_preflight`.

`PreflightReport.valid` means no static errors were found. `issues` contains
`PreflightIssue(field, message, severity)`; `to_dict()` exposes the report.
The report includes the selected task, required callback observation groups,
joint names, command size/layout, command-space flag and joint units. A joint
command still requires complete joint observations, supplied as `joints` or
per-joint position keys, even if no callback declares a `joints` parameter.

Preflight checks schema fields, active callback names, solver factory/reference
availability, layout keys/dimensions, declared units, explicit SDK fallback and
configured/referenced fallback contexts. Non-joint segment `units` can list one
unit per coordinate. Missing command units produce a visible warning; they are
not inferred from names and a valid report does not establish their semantics.
Joint degree/radian defaults match construction.

Preflight may read configuration/preset files and register builtins, but never
constructs a runtime or solver, invokes a user callback/fallback constructor,
connects hardware, starts workers, or writes recordings. It cannot establish
whether a solver will initialize, an asset/dependency is available at deployment,
a callback is correct, or a fallback is physically safe. Callers should review
warnings as well as `valid`; `raise_for_errors()` raises ConfigurationError with
field locations. Ordinary construction does not silently invoke this extra check.

### Detailed evaluation and classified errors

`rail.evaluate(inputs)` executes the same validation once and returns a
`dam.GuardrailEvaluation`. `rail(inputs)` continues to return only the command.
The detailed result includes `command`, `decision`, `guard_results`, `fallback`,
`config_version`, `task`, `cycle_id`, `verdict_age_ms` and `output_status`.
`output_status="returned"` means a command was returned to the caller; the SDK
has no sink and does not report device delivery. REJECT/FAULT can include a valid
fallback command. MONITOR may report REJECT while returning the original finite
command; `fallback` then remains None.

The result's guard evidence is copied from this evaluation. `verdict_age_ms` is
the oldest slow verdict actually used, not a later worker snapshot; individual
basis cycles and versions remain in guard-result metadata. It is None when no
aged verdict was used. `to_dict()` / `to_json()` serialize command arrays/tensors,
decisions, reasons and metadata. Non-finite diagnostic metadata is represented as
text; returned commands must already satisfy the finite-value contract.

```python
with dam.Guardrail("safety.yaml", quiet=True) as rail:
    result = rail.evaluate({"joints": measured, "action": proposed})
    print(result.decision.name, result.fallback, result.config_version)
    print(result.to_json())
    command = result.command
```

Public exception categories all derive from `dam.DamError`:

| Exception | Meaning |
| --- | --- |
| `ConfigurationError` | Construction/configuration failed |
| `InputContractError` | Missing, malformed or incompatible call inputs |
| `SessionClosedError` | Evaluation/entry attempted after close |
| `OperationTimeoutError` | An operation or worker cleanup exceeded its deadline |
| `ExecutionError` | Runtime execution or output conversion failed outside a guard result |

Wrapped failures preserve the original exception in `__cause__`. Guard rejection,
guard FAULT and a bounded slow-verdict wait that selects fallback remain evaluation
results; they are not converted into thrown execution errors. `close()` remains
retryable after a worker timeout. Input errors leave the session usable.
Applications should catch these categories instead of relying on underlying
file-loader, KeyError or dtype exceptions. The executable offline example is
`python -m examples.guardrail_evaluation`; it creates only temporary configuration
and does not connect to hardware.

### Slow-lane data ownership

Submission copies observation and action buffers, including images, channels,
nested metadata and the original proposal. The worker receives the task,
active boundary nodes, timers, command history and configuration captured on the
control thread. Do not mutate inputs during a call; after submission returns,
later caller changes do not affect that evaluation. Reading or modifying a
returned verdict does not modify the worker's cached result.

Completed results and ready/stale `slow_lane_status` include `task_name`,
`config_version` and `state_generation`. Task restart/stop and boundary advance
invalidate older generations; a mismatched verdict produces a
`slow_lane_readiness` REJECT with `slow_lane_state: version_mismatch`, and the
next call applies the first-verdict waiting policy again. This does not enable
live configuration reload while a slow worker runs.

Slow guards and solver/dynamics objects get private copies once per worker
lifetime, on first submission. A resource or metadata value that cannot be deep
copied produces a `slow_lane_snapshot` FAULT instead of using shared state.
Custom Python objects must implement independent deep copies; callbacks must
synchronize any mutable globals, closure state or external resources themselves.
The runtime and SDK remain single-caller APIs.

Slow stages run sequentially, even when their stage requests parallel execution,
so a timed-out callback cannot outlive a successfully stopped worker. The waiting
caller still has its configured deadline; Python callbacks cannot be forcibly
cancelled. Copying runs on the submitting thread and adds latency and memory
cost, especially for images and the initial model copy. Snapshot age includes
this cost; `verdict_timeout_ms` bounds the wait after copying, not total call time.
Measure with your actual images/models before choosing a control frequency.

### Motion constraint fusion

Motion corrections now preserve constraints that originally returned PASS.
Every active L1 callback participating in a correction must supply
`metadata["motion_qp"]` as a `QPTerm` (or equivalent mapping), on both PASS and
CLAMP. A single standalone custom CLAMP can still omit the metadata. Opaque
callbacks without a model cannot be combined with another motion correction;
that case produces FAULT rather than rerunning a stateful callback.

The enforced QP uses hard bounds and linear inequalities. An infeasible system,
solver failure, malformed constraint, or candidate violating the saved models
produces FAULT and selects the existing ENFORCE fallback. `slack_weight` is
accepted for compatibility, but does not relax the enforced constraints.
Custom `MotionGuard` fusion functions receive PASS and CLAMP results and must
meet the same final constraint checks.

Stages now pass their corrected candidate to the next stage. Before enforcement,
the final command is checked again against the current motion models. Opaque
L1/L2 checks accept only the exact command they checked; an unverified later
change faults. Constraint checks do not call callbacks again. See the
[safety model](concepts/safety.md) for numerical tolerance, linearization limits,
and the meaning of checks skipped because model data is unavailable.

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

Drop-in `RobotActionProcessorStep` subclass. Lazy init — the guard is created on
the first action, not at import time. LeRobot is required for its processor
pipeline; for a standalone loop, use `dam.Guardrail` directly. An importable
class alone does not verify that the LeRobot integration is available.

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
