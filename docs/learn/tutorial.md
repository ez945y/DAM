# Tutorial

Learn the DAM mental model by building one Stackfile, running it, and reading what the guard stack tells you. Takes about 30 minutes.

---

## The Problem

You have a robot and an ML policy. The policy always outputs the next action -- it has no idea when that action is unsafe. In simulation everything looks fine. On hardware, the policy grabs the wrong thing, bumps into objects, or pushes joints too hard.

DAM intercepts every action and tells you what happened: **passed**, **clamped** (adjusted to stay safe), or **rejected** (replaced by a fallback). The goal is not to hide failures -- it is to make them visible.

---

## Step 1: Understand the Guard Stack

Four independent layers evaluate every action:

```
Policy Output
    ↓
[ L0 OOD ]        Is the observation familiar?
[ L1 Motion ]     Are joints and workspace safe?
[ L2 Task ]       Does this fit the task?
[ L3 Hardware ]   Is the robot healthy?
    ↓
DECISION: Pass / Clamp / Reject
```

The most restrictive decision wins. If L1 says CLAMP and L2 says REJECT, the action is rejected.

**Fail-to-reject:** if a guard crashes, times out, or throws an exception, the action is rejected. The system is conservative by default.

For a deeper look at each layer, see [Guard Stack Explained](../concepts/guards-explained.md).

---

## Step 2: Write a Minimal Stackfile

A **Stackfile** is a YAML file that configures everything DAM needs -- guards, boundaries, tasks -- without writing Python code.

Start from the minimum:

```yaml
dam:
  version: "1"

guards:
  - L1: motion
    phase: 0
  - L2: execution
    phase: 1
  - L3: hardware
    always: true

boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]

tasks:
  demo:
    boundaries: [joint_position_limits]
```

This enables L1 motion guard with joint limits. Any action that pushes a joint past 1.57 rad gets clamped back.

Validate it:

```bash
.venv/bin/dam validate my_stackfile.yaml
```

For full Stackfile options, see [Stackfile Walkthrough](../getting-started/stackfile-walkthrough.md) and [Common Edits](../getting-started/common-stackfile-edits.md).

---

## Step 3: Add Boundaries

Boundaries define the safety envelope. Two types:

**Single** -- one set of constraints for the entire task:

```yaml
boundaries:
  joint_velocity_limit:
    layer: L1
    type: single
    nodes:
      - callback: joint_velocity_limit
        params:
          max_velocities: [1.5, 1.5, 1.5, 1.5, 1.5, 0.5]
```

**List** -- multiple phases, advanced sequentially:

```yaml
boundaries:
  pick_and_place:
    layer: L2
    type: list
    nodes:
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.35, 0.35], [-0.05, 0.45], [0.01, 0.40]]
        fallback: hold_position
        timeout_sec: 15.0
      - callback: task_workspace_bounds
        params:
          bounds: [[-0.20, 0.20], [0.05, 0.35], [0.01, 0.15]]
        fallback: hold_position
        timeout_sec: 8.0
```

When a constraint is violated, the fallback fires: `hold_position`, `retreat`, or `emergency_stop`.

For boundary design patterns, see [Boundary System](../concepts/boundaries.md).

---

## Step 4: Run It

```bash
make run   # Backend :8080 + Console :3000
```

Or from Python:

```python
import dam

summary = dam.run("my_stackfile.yaml", task="demo", cycles=200)
print(summary.status, summary.cycles, summary.emergency)
```

Open the console at `http://localhost:3000` and watch guard decisions in real time.

---

## Step 5: Read What Happened

Every cycle produces telemetry. The console shows it live; the API lets you query it:

```bash
curl http://localhost:8080/api/telemetry/history     # last N cycles
curl http://localhost:8080/api/risk-log/stats         # aggregate stats
curl http://localhost:8080/api/risk-log/export/json   # full export
```

When an action is rejected, DAM captures +/-30 seconds of context in MCAP format for post-incident analysis.

The key question is not "did it work?" It is: **where did the policy start to break down, and which guard caught it?**

---

## Step 6: Prepare for Hardware

Before connecting real hardware, work through this checklist:

- [ ] Stackfile validates: `dam validate mystack.yaml`
- [ ] Demo runs without errors in simulation
- [ ] Fallbacks tested: manually trigger rejections and verify hold/retreat/e-stop
- [ ] Cycle latency under budget (< 5ms at 50 Hz)
- [ ] Physical e-stop button accessible and tested

See [Hardware Readiness](../getting-started/hardware-readiness.md) for the full checklist.

---

## What You Now Know

- The guard stack evaluates every action through four independent layers
- Fail-to-reject: when in doubt, reject
- Stackfiles configure guards, boundaries, and tasks in YAML
- Boundaries define the safety envelope; list boundaries model multi-phase tasks
- The console and API show you where the policy breaks down, not just whether it succeeded

---

## Next Steps

- [Use Cases](../concepts/use-cases.md) -- how DAM helps at each stage of the ML pipeline
- [Guard Stack Explained](../concepts/guards-explained.md) -- deep dive into each layer
- [Safety Model](../concepts/safety.md) -- what DAM does and does not guarantee
- [Troubleshooting](../getting-started/troubleshooting.md) -- common first-run issues
- [Glossary](glossary.md) -- quick term reference
