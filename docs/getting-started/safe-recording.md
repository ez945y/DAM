# Safe Recording

Guard every action during imitation learning data collection. Bad frames get caught before they enter your dataset, not after a policy learns from them.

---

## Quick Start

```bash
make record
```

This runs `scripts/record.py` with [`examples/stackfiles/safety.yaml`](https://github.com/ez945y/DAM/blob/main/examples/stackfiles/safety.yaml) -- one file that configures hardware, safety boundaries, and recording parameters.

Override any setting via CLI:

```bash
make record ARGS="--dataset.num_episodes=20"
make record ARGS="--stackfile=my_safety.yaml"
```

---

## The Stackfile

Everything lives in one YAML file. Three sections:

```yaml
# 1. Hardware — robot, cameras, teleop
hardware:
  preset: so101_follower
  sources:
    arm: { type: motor, port: /dev/tty.usbmodem... }
    top: { type: opencv, index_or_path: 0 }
  teleop:
    type: so101_leader
    port: /dev/tty.usbmodem...
  sinks:
    command: { ref: sources.arm }

# 2. Safety boundaries
boundaries:
  joint_position_limits:
    layer: L1
    type: single
    nodes:
      - callback: joint_position_limits
        params:
          upper: [1.57, 1.57, 1.57, 1.57, 1.57, 0.08]
          lower: [-1.57, -1.57, -1.57, -1.57, -1.57, 0.0]

# 3. Recording config (forwarded to lerobot-record)
recording:
  dataset:
    repo_id: ${HF_USER}/my_dataset
    num_episodes: 10
  display:
    cameras: [top]
```

Edit the stackfile, not the code. `make record` reads it and does the rest.

---

## Python API

Three integration levels, from simplest to most control:

### Level 1: `dam.safe()` -- one-liner

```python
import dam

safe_action = dam.safe(action, obs, stackfile="safety.yaml")
```

Stateless. Good for notebooks and quick scripts.

### Level 2: `dam.SafetyGuard` -- stateful guard

```python
guard = dam.SafetyGuard("safety.yaml", task="record")

for action, obs in teleop_stream:
    safe_action = guard(action, obs)
    # dict in -> dict out, ndarray in -> ndarray out
    # rejected actions return hold-position (no interruption)
```

Auto-detects `joint_names` and `degrees_mode` from the preset. Keeps state across calls for velocity/acceleration checks.

### Level 3: `SafetyProcessorStep` -- LeRobot pipeline

```python
from dam import SafetyProcessorStep

# Add one line to your existing pipeline
robot_action_processor.steps.insert(0, SafetyProcessorStep("safety.yaml"))
```

Or use the convenience wrapper:

```python
from dam.processor import make_safe_processors

teleop, robot_action, obs = make_safe_processors("safety.yaml")
```

Lazy initialization -- the guard is created on the first call, not at import time.

---

## What Happens During Recording

When an operator's command hits a boundary:

| Situation | What DAM does | What the operator feels |
|-----------|---------------|------------------------|
| Joint exceeds position limit | Clamp to limit | Arm resists slightly |
| Velocity too high | Scale all joints proportionally | Motion slows down smoothly |
| End-effector approaching workspace boundary | CBF clamp via QP | Motion steered away from boundary |

The clamped action is what gets recorded. Your dataset contains only actions that respect all configured boundaries.

---

## Monitoring

While recording, watch the DAM Console at `http://localhost:3000` for:

- Real-time guard decisions (pass / clamp / reject)
- Which boundaries are triggering and how often
- Clamp rates per joint -- a high rate often signals a boundary set too tight or a loose calibration

MCAP logs capture +/-30 seconds of context around every violation for post-session review.

---

## Example

See [`examples/safe_record.py`](https://github.com/ez945y/DAM/blob/main/examples/safe_record.py) for a runnable demo of all three API levels without hardware.

---

## Next Steps

- [Common Stackfile Edits](common-stackfile-edits.md) -- adjust boundaries and recording settings
- [Hardware Readiness](hardware-readiness.md) -- full pre-flight checklist
- [Use Cases](../concepts/use-cases.md) -- how DAM helps beyond data collection
