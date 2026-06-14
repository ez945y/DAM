# DAM 0.7.0 Release Notes

0.7.0 is a semantics release. The Stackfile and preset model grew several
overlapping ways to say the same thing — a preset that carried limits *and*
boundaries that carried limits, `sources`/`sinks` *and* capability flags,
`chains` *and* `action_layout`, a fallback `type` field that always equalled
its own key. This release picks one of each and deletes the rest. There is no
compatibility shim: old Stackfiles must be updated.

It also promotes the Python registration surface — presets, callbacks, solvers,
interfaces, and fallbacks are now first-class `dam.register_*` / `@dam.*` APIs,
so a custom embodiment can be defined entirely from code without editing
bundled YAML.

## `Guardrail` — dict in, validated command out

`SafetyGuard(action, obs)` took two **flat joint vectors**, which forced any
embodiment whose observation and action are different spaces (a mobile base
observes a pose `[x, y, yaw]` but commands a twist `[v, omega]`) to pack
everything into one vector and slice it back out positionally. The filter API is
now a single keyed dict aligned with the runtime's `step()`:

```python
rail = dam.Guardrail("jetbot.yaml", safe_action=[0.0, 0.0])
# prints its contract once:
#   requires obs: base_pose
#   action:       [v, omega]  (command space)

safe_cmd = rail({"base_pose": [x, y, yaw], "action": [v, omega]})
```

One rule: **whatever key you put in the dict, a callback receives by declaring a
parameter of the same name.** `action` is reserved (the command to validate);
everything else is an observation group, injected by name — no positional
slicing:

```python
@dam.callback("forward_only", layer="L1")
def forward_only(*, base_pose, action, solvers, dt=0.1):
    x_next, *_ = solvers["ackermann"].rollout(base_pose, action, dt)
    return bool(x_next >= 0.0)
```

The guard prints the obs/action contract it expects on construction and
**fails fast** if an input dict omits a required group. On reject it returns
`safe_action` — an explicit stop vector, `"hold"` (default; current joint
positions, safe for a position-controlled arm), or `"zero"`. See
`examples/mobile_base_guardrail.py` — the whole integration is ~40 lines.

**Renames (no shim):** `SafetyGuard` → `Guardrail`, `dam.safe()` →
`dam.guardrail()`, `SafetyProcessorStep` → `GuardrailProcessorStep`. Call sites
change from `guard(action, obs)` to `guard({**obs, "action": action})`.
`Observation.joint_positions` is now optional (a mobile base has none; its state
lives in `obs.channels`).

## Preset = robot identity only

A preset now carries **only what is intrinsic to a robot model**: `name`,
`joint_names`, `asset` (URDF/USD), `solvers`, and `action_layout`. Everything
that is a *policy* about how to operate the robot has moved out:

- **Limits / max velocities / gripper handling** live on boundary callbacks in
  the Stackfile — never on the preset.
- **`degrees_mode`** is an *interface* concern, not robot identity. Declare it
  on the motor interface (`interfaces.<name>.degrees_mode`) or, for
  interface-less configs (a tensor-only `Guardrail`), on
  `hardware.degrees_mode`. lerobot motors are degree-native (default `true`);
  radian-native robots (e.g. Franka) set it `false`.

## Capability-based interfaces

`hardware.sources` and `hardware.sinks` are replaced by a single
`hardware.interfaces` map. Each interface declares what it exposes with
`capabilities`; the runtime lowers that into internal read/write endpoints, so
a command sink is no longer a separate stanza — it is the `command_joints`
capability on the motor interface.

```yaml
# 0.6.0
hardware:
  sources:
    arm:    { type: motor, port: /dev/ttyUSB0, robot_type: so101_follower }
    cam:    { type: opencv, index_or_path: 0 }
    current:{ type: current, ref: arm }
  sinks:
    command: { ref: sources.arm }

# 0.7.0
hardware:
  interfaces:
    arm:     { type: motor,  capabilities: [observe_joints, command_joints], port: /dev/ttyUSB0, robot_type: so101_follower }
    cam:     { type: opencv, capabilities: [image], index_or_path: 0 }
    current: { capabilities: [robot_telemetry], ref: arm }
```

Capability vocabulary: `observe_joints`, `command_joints`, `image`,
`robot_telemetry`. Telemetry interfaces that share a motor's bus reference it
with `ref` and need no `type`.

## `action_layout` replaces `chains` / `JointLayout`

The kinematic-`chains` map and the `JointLayout` type are removed. They
overlapped with `action_layout`, which already names segments of the policy
action vector (joint group, end-effector pose, differential base) and links
each to a solver. There is now one named-segment contract, not two. The FK
joint-name alignment that previously read `JointLayout.names` now reads the
preset's `joint_names` directly — no behaviour change to kinematics.

`input_space` (on both `hardware` and `policy`) is gone with it: the action
space is implied by the preset's `action_layout`.

## Fallback `type` removed

A fallback entry's map key **is** the builtin Context it resolves to, so the
redundant `type` field is rejected (`extra="forbid"`):

```yaml
# 0.6.0                  # 0.7.0
fallbacks:               fallbacks:
  hold_position:           hold_position:
    type: hold_position      severity: 80
    severity: 80
```

Built-in names: `emergency_stop`, `hold_position`, `wait_and_retry`,
`slow_down`, `retreat`. Optional `escalate_to` / `escalate_after_seconds`
auto-escalate an ambiguous trigger up the chain; `emergency_stop` is terminal.

## First-class registration API

Defining a custom embodiment no longer requires touching bundled YAML. The
public surface (`import dam`):

- `register_preset(name, joint_names=…, asset=…, solvers=…, action_layout=…)`
- `register_solver(...)` for a live solver object
- `register_callback(...)` and the `@dam.guard` / `@dam.callback` /
  `@dam.fallback` / `@dam.solver_factory` decorators
- `register_read_interface` / `register_write_interface` /
  `register_robot_telemetry_interface` / `register_host_telemetry_interface`

**Solver factories are now ergonomic.** `register_solver_factory` is replaced by
the `@dam.solver_factory` decorator, and factories declare the params they need
as keyword arguments instead of digging through a `params` dict. DAM injects
matching values from the stackfile `params` plus robot context (`asset_path`,
`observation_joint_names`, …) and drops anything the factory does not declare:

```python
# 0.6.0
@dam.register_solver_factory("ackermann", capabilities=["rollout"])
def make(params):
    return AckermannSolver(wheel_base=params.get("wheel_base"))

# 0.7.0
@dam.solver_factory("ackermann", capabilities=["rollout"])
def make(wheel_base=None, track_width=None):
    return AckermannSolver(wheel_base, track_width)
```

## Migration checklist

1. **Fallbacks** — delete every `type:` line under `fallbacks.*` (the key is
   the type).
2. **Interfaces** — rename `hardware.sources` → `hardware.interfaces`, add a
   `capabilities:` list to each, and fold `hardware.sinks` into the owning
   interface's `command_joints` capability.
3. **Presets** — remove `degrees_mode` from presets; declare it on the motor
   interface instead. Move any limits off presets onto boundary callbacks.
4. **`chains` / `input_space`** — delete both; rely on the preset's
   `action_layout`.
5. **Solver factories** — replace `@dam.register_solver_factory` /
   `dam.register_solver_factory(...)` with `@dam.solver_factory`, and change the
   factory signature from `(params)` to the keyword params it actually needs.
6. **Filter API** — `SafetyGuard`/`safe`/`SafetyProcessorStep` →
   `Guardrail`/`guardrail`/`GuardrailProcessorStep`; change `guard(action, obs)`
   to `guard({**obs, "action": action})`. Custom filter callbacks read obs
   groups by parameter name (`base_pose`, `current`, …) and tag their `@dam.callback`
   with `layer=`.
