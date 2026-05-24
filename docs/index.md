# DAM — Detachable Action Monitor

**Safety middleware for ML-driven robot control**

DAM sits between a policy and robot hardware. Every proposed action is checked by guard layers before it reaches the actuator, then DAM either **passes**, **clamps**, or **rejects** the action and records what happened.

Use DAM when you want to change safety rules, task boundaries, or monitoring behavior without retraining the policy or rewriting hardware drivers.

---

## Start Here

| Goal | Go to | Done when |
|------|-------|-----------|
| Run DAM without hardware | [Quick Start](getting-started/quickstart.md) | Console opens and example Stackfiles validate |
| Learn the system step by step | [Learn DAM](learn/index.md) | You can explain pass, clamp, reject, and fallback |
| Read a Stackfile | [Stackfile Walkthrough](getting-started/stackfile-walkthrough.md) | You can point to hardware, policy, guards, boundaries, and tasks |
| Monitor a run | [DAM Console](console.md) | You can find the latest guard decision and latency state |
| Fix first-run issues | [Troubleshooting](getting-started/troubleshooting.md) | You know whether the issue is setup, ports, validation, or task naming |

---

## What DAM Checks

DAM organizes safety into four guard layers:

| Layer | Question |
|-------|----------|
| L0 OOD | Is the observation familiar enough to trust the policy? |
| L1 Motion | Are joint limits, velocity limits, and workspace constraints safe? |
| L2 Task | Does the command fit the active task phase? |
| L3 Hardware | Is the robot and host environment healthy? |

The most restrictive decision wins: `REJECT` beats `CLAMP`, and `CLAMP` beats `PASS`.

For the deeper model, read [Guard Stack Explained](concepts/guards-explained.md).

---

## Typical Workflow

1. Configure a Stackfile.
2. Validate it with `dam validate` or `make validate`.
3. Run DAM with the selected task.
4. Watch the console for pass, clamp, reject, latency, and risk state.
5. Use logs and MCAP sessions to inspect safety events.

Start with the no-hardware demo before moving to real robot hardware.

---

## Why Teams Use It

- Safety rules live in versioned configuration.
- Built-in guard layers can be enabled per task.
- Fail-to-reject behavior makes guard failures conservative.
- MCAP loopback logs help replay and audit safety events.
- LeRobot, ROS 2, and dataset-style workflows can share the same safety model.

---

## Safety Status

DAM is research and experimental-grade software. It is not certified for safety-critical production use or unsupervised human-collaborative environments. Treat it as a safety research and development tool, validate your Stackfiles carefully, and keep hardware emergency procedures in place.

---

## Reference

| Need | Page |
|------|------|
| Install details | [Installation](installation.md) |
| Full Stackfile fields | [Stackfile Guide](quick-stack.md) |
| CLI commands | [CLI](cli.md) |
| Guard catalog | [Guards Reference](guards-reference.md) |
| Boundary callbacks | [Boundary Callbacks](boundary-callbacks.md) |
| API details | [Services API](services-api.md) |
| Architecture reference | [Specification](DAM_Specification.md) |
