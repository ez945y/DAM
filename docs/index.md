# DAM — Detachable Action Monitor

**See where your policy breaks before your hardware does.**

ML policies look fine in simulation. Then they hit real hardware — and you have no idea which commands are unsafe, which motions are out of distribution, or where the policy's learned behavior falls apart. Failures are silent until they're physical.

DAM sits between a policy and robot hardware and makes those boundaries visible. Every proposed action passes through guard layers that **pass**, **clamp**, or **reject** it — and every decision is recorded. The point isn't to hide failures. It's to make them visible and easier to understand.

---

## Where DAM Helps

**Data collection.** DAM smooths motions during teleoperation and flags segments where commands hit limits. Bad data gets caught before it enters your dataset, not after a policy learns from it.

**Training.** When a policy explores bad behaviors — joint limits, workspace violations, unfamiliar observations — DAM logs exactly what went wrong. You see which training runs produce policies that respect boundaries and which don't.

**Deployment.** This is where DAM matters most. Instead of binary pass/fail, you see a continuous picture of where the policy works confidently and where it breaks down. Guard events, clamp rates, and risk state tell you what to fix next.

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

## What DAM Watches

Four guard layers, each asking a different question about every action:

| Layer | Question |
|-------|----------|
| L0 OOD | Is the observation familiar enough to trust the policy? |
| L1 Motion | Are joint limits, velocity limits, and workspace constraints respected? |
| L2 Task | Does the command fit the active task phase? |
| L3 Hardware | Is the robot and host environment healthy? |

The most restrictive decision wins: `REJECT` beats `CLAMP`, and `CLAMP` beats `PASS`. For the deeper model, read [Guard Stack Explained](concepts/guards-explained.md).

---

## Safety Status

DAM is research and experimental-grade software. It is not certified for safety-critical production use or unsupervised human-collaborative environments. Treat it as a development and research tool, validate your Stackfiles carefully, and keep hardware emergency procedures in place.

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
