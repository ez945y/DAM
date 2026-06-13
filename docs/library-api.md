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
dam.register_callback(name, fn=None, *, layer, category, description, params)
dam.register_solver(name, solver, *, capabilities)
dam.register_solver_factory(type, factory, *, capabilities)
dam.register_read_interface(type, factory)
dam.register_write_interface(type, factory)
@dam.callback(name)                         # register a boundary callback
@dam.guard(layer, *, phase, always)         # register a Guard subclass
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

- Auto-detects `joint_names` from `hardware` or `hardware.preset`, and `degrees_mode` from the motor interface (`source.degrees_mode` / `hardware.degrees_mode`)
- Rejected actions return hold-position (current joint positions) so loops never break
- Access the underlying runtime via `guard.runtime`

`SafetyGuard` exposes action semantics from `hardware.action_layout` or the
selected preset to callbacks. Each segment lists its `keys` — what each slot
means, in order — so the segment size is self-describing. It does not
automatically run IK/FK; callbacks choose a solver and the solver decides
whether it supports that action segment.

```python
@dam.register_callback("my_ee_rule", layer="L1")
def my_ee_rule(*, action, action_layout, solvers):
    ee_segment = action.metadata["action_segments"]["arm"]
    solver = solvers["arm_kinematics"]
    if not solver.supports("ee_pose"):
        return True

    joint_target = solver.inverse_kinematics(
        ee_segment,
        action.target_joint_positions,
    )
    ...
    return True

guard = dam.SafetyGuard(
    "safety.yaml",
    solvers={"arm_kinematics": ArmSolver()},
)

safe_action = guard(policy_action, current_joint_positions)
```

Stackfile:

```yaml
hardware:
  action_layout:
    - name: arm
      keys: [x, y, z, yaw, pitch, roll]   # 6-DoF EE pose; size == len(keys)
      solver: arm_kinematics
    - name: gripper
      keys: [gripper]
```

Solvers are the extension point. A preset or stackfile `hardware` block can own
multiple solver definitions: arm kinematics, base dynamics, collision checking,
map constraints, and more. The robot description is a single `asset`; solvers
receive that asset path automatically unless they override params explicitly.

### `dam.SafetyProcessorStep` — LeRobot integration

```python
from dam import SafetyProcessorStep

robot_action_processor.steps.insert(0, SafetyProcessorStep("safety.yaml"))
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
      arm_kinematics:
        type: pinocchio_kinematics
        capabilities: [fk, ik]
    action_layout:
      - name: arm
        keys: [x, y, z, yaw, pitch, roll]
        solver: arm_kinematics
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

Pass it into `SafetyGuard` by name or object map:

```python
guard = dam.SafetyGuard("safety.yaml", solvers={"arm_kinematics": isaac_solver})
```

### `dam.register_solver_factory(...)`

Register a Stackfile-instantiable solver factory:

```python
def make_usd_solver(params):
    return IsaacUsdSolver(params["prim_path"])

dam.register_solver_factory(
    "isaac_usd",
    make_usd_solver,
    capabilities=["kinematics"],
)
```

Stackfile:

```yaml
hardware:
  preset: my_arm
  solvers:
    arm:
      type: isaac_usd
      capabilities: [kinematics]
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
