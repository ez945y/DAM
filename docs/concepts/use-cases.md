# Where DAM Helps: Data Collection, Training, and Deployment

DAM sits between ML policies and robot hardware. Every action passes
through a guard stack (L0 OOD, L1 Motion, L2 Task, L3 Hardware) before
reaching actuators. Actions are **passed**, **clamped** (adjusted to
stay within bounds), or **rejected** (replaced by a fallback).

The point of all this is not really "safety." It is helping you
understand the boundaries of a policy before something goes wrong.

---

## 1. Data Collection

### The problem

During teleoperation or scripted data collection, operators send
commands that occasionally exceed joint limits, spike velocities, or
drift into configurations the downstream policy should never learn from.
These bad frames end up in datasets and silently degrade policy quality.

### How DAM helps

Insert a `GuardrailProcessorStep` into a LeRobot recording pipeline. DAM
evaluates every action through the guard stack and clamps out-of-bound
commands before they reach hardware -- and before they land in your
dataset.

```python
from dam import GuardrailProcessorStep

# Drop into any LeRobot robot_action_processor pipeline
robot_action_processor.steps.insert(0, GuardrailProcessorStep("safety.yaml"))
```

Operators get immediate tactile feedback: when a motion is clamped, the
arm resists slightly rather than slamming into a hard stop. Over a
session this builds intuition for which regions of the workspace produce
clean data and which do not.

### What you will see

- **DAM Console** highlights clamped actions in real time, showing which
  guard layer triggered and by how much the command was adjusted.
- **MCAP logs** capture +/-30 seconds of context around every violation,
  so you can replay the exact moment an operator drifted out of bounds.
- A rising clamp rate on a particular joint often signals a loose
  calibration or a workspace boundary set too tight -- actionable before
  you train on bad data.

---

## 2. Training and Evaluation

### The problem

During offline evaluation or sim-to-real transfer testing, policies
explore freely. Some produce obviously bad actions -- jerky oscillations,
joint-limit collisions, physically implausible velocities -- but without
instrumentation you only notice when hardware breaks or metrics plateau
for unclear reasons.

### How DAM helps

Wrap policy outputs with `Guardrail` to validate actions without a
hardware loop. This works in offline eval scripts, batch rollouts, or
anywhere you can call Python.

```python
import dam

guard = dam.Guardrail("safety.yaml")

for action, obs in rollout:
    safe_action = guard({**obs, "action": action})
    if guard.last_results:  # inspect what happened
        print(guard.last_results)
```

Each call returns the safe version of the action (clamped or replaced
with a hold-position fallback if rejected). `guard.last_results` gives
you the full list of `GuardResult` objects from that cycle -- which layer
fired, the decision, and the magnitude of any clamp.

### What you will see

- **Clamp-rate curves** across training checkpoints: a policy that
  triggers fewer guard interventions over time is actually learning safe
  behavior, not just optimizing reward.
- **Per-layer breakdown**: L1 clamps (velocity/position limits) dropping
  while L2 rejects (task-phase violations) remain high tells you the
  policy learned kinematics but not task structure.
- **MCAP session replay** lets you scrub through a rollout and see
  exactly where the policy started producing actions the guard stack
  could not pass -- often more informative than aggregate loss curves.

---

## 3. Deployment

### The problem

A policy that works in the lab may encounter edge cases in deployment:
novel objects, shifted camera angles, accumulated drift. The most useful
signal at this stage is not whether the policy succeeds -- it is
understanding where and how it starts to break down.

### How DAM helps

Run the full managed loop with `dam.run()` or the CLI. The guard stack
evaluates every cycle in real time, and the fallback engine handles
rejected actions (hold position, slow retreat, emergency stop) based on
your stackfile routing.

```python
import dam

summary = dam.run("deploy.yaml", task="pick_place", cycles=-1)
print(summary.status, summary.cycles, summary.emergency)
```

The `-1` cycle count runs until the task completes or a terminal
condition fires. On exit, `RunSummary` tells you whether the run ended
cleanly, how many cycles executed, and whether an emergency stop
occurred.

### What you will see

- **DAM Console** streams guard decisions live. You can watch the policy
  operate and see the moment L0 OOD confidence drops or L2 task-phase
  guards start clamping -- before the arm does anything visibly wrong.
- **Fallback transitions** are logged as context events in MCAP. A
  sequence like `Normal -> SlowDown -> HoldPosition -> Normal` tells you
  the policy recovered; `Normal -> EmergencyStop` tells you it did not.
- **Post-session triage** with `mcap_triage.py` summarizes guard
  outcomes, clamp rates, and hardware events so you can identify failure
  patterns without scrubbing raw logs.

---

## What DAM Won't Do

!!! warning "Limitations"

    **DAM does not fix your policy.** It tells you where the policy
    produces unsafe or out-of-distribution actions. You still need to
    improve the policy, fix calibration, or adjust the task definition.

    **DAM does not replace hardware e-stop.** Software-layer guards add
    defense in depth, but a physical emergency stop button on the robot
    remains a hard requirement for any real-world deployment.

    **DAM is not certified for production safety.** It is a research and
    development tool. It has not been through IEC 61508, ISO 13849, or
    any formal safety certification process. Do not rely on it as the
    sole safety mechanism for unattended operation.
