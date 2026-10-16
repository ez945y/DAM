# Safety Model

No software layer can truly "guarantee safety." But if we cannot clearly see the
limits of a policy, it is hard to build robot learning systems we can actually
trust.

DAM sits between ML policies and robot hardware. Its job is not to certify that
a system is safe -- it is to make failures **visible** and **understandable**.
When a policy proposes something dangerous, DAM intercepts it, logs what
happened, and gives you the data to figure out why.

DAM is research software. Treat it as an observability and guardrail layer, not
a certified safety system.

---

## What DAM Does NOT Guarantee

Read this section first. Understanding these limits is more important than
understanding the features.

1. **Policy safety.** DAM validates actions, not policies. A policy that
   hallucinates plausible-looking but wrong actions may slip past guards.

2. **Collision avoidance.** DAM has no built-in collision checker. Use task
   boundaries (L2) to constrain reachable workspace as a proxy, or integrate a
   dedicated collision-checking callback.

3. **Human safety in collaborative tasks.** DAM is not certified for
   human-robot collaboration. It cannot detect human presence, predict human
   motion, or comply with ISO/TS 15066 on its own.

4. **Adversarial robustness.** DAM assumes sensor data is honest. Spoofed
   observations may produce unsafe guard decisions.

5. **Formal verification.** The system is experimental-grade. There is no formal
   proof of correctness.

---

## Design Principles

### Fail-to-Reject

The most important rule: when a guard cannot reach a clear decision, the action
is rejected.

```python
try:
    decision = guard.evaluate(action, observation, state)
except Exception:
    decision = REJECT   # exception, timeout, memory error -> REJECT
```

Guard exceptions, timeouts, and corrupt data all produce rejections. The system
is conservative by default: do not execute an action when the guard stack cannot
evaluate it reliably.

Missing callbacks, malformed callback results, and missing or invalid clamp
payloads produce `FAULT`. Clamp fusion failures remain faults when results are
split back into per-boundary records. Custom callbacks must return `bool`,
`(bool, reason)`, `CallbackResult`, or `GuardResult`; arbitrary truthy values
are not accepted as passes.

When motion callbacks require a correction, the fusion input includes constraints
from both PASS and CLAMP callbacks. Every participating callback must supply
`motion_qp` metadata; a single standalone custom CLAMP can omit it. Missing
constraint metadata during fusion produces FAULT. Built-in callbacks retain
their box, linear, and EE-speed norm models even when the original command passes.

The enforced fusion solver uses hard constraints. Infeasible limits, failed
solves, malformed matrices, and a returned candidate outside any constraint
produce FAULT instead of an executable compromise. `slack_weight` remains
accepted for compatibility but no longer permits relaxed enforcement. The legacy
standalone soft solver remains available for experiments.

Candidate checks are pure evaluations of saved constraint data: callbacks are
not invoked a second time. Position and normalized linear residuals allow an
absolute `1e-7` tolerance; EE-speed norm residuals use `1e-7` in speed units.
Linear rows are normalized by their largest absolute coefficient. Stage outputs
feed subsequent stages, and the final merged command is checked again against
the current motion constraints. Opaque L1/L2 results are bound to the exact
position, velocity, and gripper command they accepted; a later unverified
modification faults. Generic merging also faults on incompatible shapes,
different proposal baselines, or opposing corrections.

These checks establish consistency with the declared models, not exact nonlinear
robot behavior. Workspace, keep-out, and orientation checks remain locally
linearized. The EE-speed norm check can conservatively reject a candidate when
the tangent QP fails to find a norm-feasible correction. Existing callbacks that
skip a check due to missing model data contribute an empty constraint; that
does not certify the skipped physical condition. Cached asynchronous PASS still
describes its earlier snapshot, as documented below.

### Defense-in-Depth

Four independent guard layers evaluate every proposed action from different
angles. The most restrictive decision wins. A failure in one layer does not
compromise the others.

```
Observation
    |
[ L0 - OOD Detection ]        Is this state familiar?
    |
[ L1 - Physical Kinematics ]  Are joint/velocity/workspace limits respected?
    |
[ L2 - Task Execution ]       Does the action fit the current task phase?
    |
[ L3 - Hardware Monitoring ]   Is the hardware healthy?
    |
Action (or rejection)
```

---

## The Four Guard Layers

Each layer is documented in detail in [Guard Stack Explained](guards-explained.md).
Configuration examples live in [Boundary System](boundaries.md). Below is a
summary of what each layer contributes to the safety model.

**L0 -- OOD Detection.** Flags observations that fall outside the distribution
the policy was trained on. Uses a memory bank (nearest-neighbor distance) or a
Welford z-score fallback. Statistical by nature -- false negatives and false
positives are expected.

**L1 -- Physical Kinematics.** Enforces hard kinematic and dynamic constraints:
joint position limits, velocity limits, acceleration limits, and workspace
bounds. Position and velocity violations are clamped; workspace violations are
rejected outright.

**L2 -- Task Execution.** Enforces task-level constraints defined as boundary
callbacks -- workspace bounds scoped to a task phase, gripper clearance checks,
phase timeouts. Callback correctness is the user's responsibility.

**L3 -- Hardware Monitoring.** Queries the hardware sink for motor faults,
temperature, watchdog status, and connectivity. Rejects actions when any health
check reports an unhealthy state. DAM cannot prevent hardware faults; it can
only react to reported signals.

---

## Runtime Safety Considerations

### Rust Data Plane

The Rust layer handles the real-time critical path: observation multiplexing,
action evaluation, and decision caching. Rust eliminates memory-safety classes
like use-after-free and data races, and reduces GIL-related timing noise. Actual
cycle timing must still be measured on the target machine and monitored via the
console.

### Python Fallback

If the Rust extension is not compiled, the same guard semantics run in Python
with more timing variability. Validate latency on your hardware before relying
on the Python path.

### Asynchronous Guard Freshness

Slow-lane freshness is measured from snapshot publication, including queue and
evaluation time. A late PASS is not treated as fresh merely because its
computation just finished. Evaluation exceptions and malformed results publish
FAULT immediately instead of retaining an earlier PASS.

The first asynchronous cycle has no unchecked grace period. By default it
waits up to `slow_lane.verdict_timeout_ms` (500 ms) for its own verdict before
the final enforcement decision. `first_verdict: reject` instead rejects while
the first verdict is pending. Missing first evidence always rejects in ENFORCE
mode, even with `stale_action: warn`. Waiting callers also reject stale evidence.
Fast callbacks run once, and the slow check sees the fast candidate before
state commit or sink output. Normal runtime `step()` enables this gate even
though the outer step owns recording and state commits.

The one-shot SDK helper always waits for its own slow verdict. Long-lived
sessions use cached slow evidence after startup unless `wait_for_slow=True` is
selected. Cached PASS does not establish that the current action was checked:
inspect `basis_cycle_id` and `current_cycle_checked` on slow results. Public
`slow_lane_status` reports availability and age, not safety. MONITOR records
violations without blocking commands, and LOG_ONLY bypasses checks.

A slow CLAMP is still escalated to REJECT; no asynchronous correction is applied
to the fast candidate. Waiting does not resolve the separate constraint fusion,
snapshot ownership, or fallback-output validation work in the
[roadmap](../roadmap.md). In particular, fallback contexts retain their existing
validation behavior; this change gates the normal proposal path. Evaluation
deadlines cannot forcibly stop Python callbacks, so shutdown may separately
time out and raise rather than report successful cleanup.

Task switching waits for the previous evaluator to stop before changing task
state. If stopping times out, the operation raises and retains the worker
reference and current task state. Retry after the evaluator has exited; a new
worker must not overlap the old one.

### Hot-Reload

Hot-reload supports boundary node `params` updates at the start of the next
`step()` or `validate()` cycle. Both the shared parameter pool and callback
node parameters are replaced together, while the current phase and node timers
are preserved. Preparation failures keep the previous configuration and version.

Changes to callbacks, boundary structure, tasks, hardware, guards, or safety
settings require restarting the runtime; they are rejected rather than partially
applied. Reloads are also rejected while a slow-lane worker is running, to avoid
mixing old asynchronous verdicts with new limits. Stop the runtime before making
those changes. Check the reload log and configuration version to confirm that
an update took effect.

---

## Practical Safety Recommendations

1. **Enable all four layers.** Do not rely on a single guard. Defense-in-depth
   only works when the layers are actually running.

2. **Start with tight boundaries.** Begin with conservative limits and loosen
   them incrementally as you validate behavior.

3. **Monitor the MCAP buffer.** When a reject or clamp occurs, analyze the
   surrounding context. The risk log and MCAP replay give you the evidence to
   understand what happened.

4. **Test fallbacks before hardware.** Force rejections in simulation to verify
   that fallback behaviors (hold-position, safe-retreat, etc.) do what you
   expect.

5. **Validate your Stackfile.** Run `dam validate mystack.yaml` before loading.
   Schema errors caught early are cheaper than surprises on hardware.

---

## Next Steps

- [Guard Stack Explained](guards-explained.md) -- how each guard works and how
  to configure it
- [Boundary System](boundaries.md) -- defining and composing safety boundaries
- [Hardware Readiness](../getting-started/hardware-readiness.md) -- preparing
  hardware for safe operation
- [DAM Console](../console.md) -- real-time monitoring and diagnostics

---

DAM is experimental-grade software. For safety-critical deployments, combine it
with formal methods, extensive testing, independent hardware safety systems, and
human oversight.
