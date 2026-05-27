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

### Hot-Reload

When a Stackfile changes at runtime, DAM parses, validates, and swaps the
config atomically at the start of the next cycle. Partial or invalid configs are
not applied. Verify hot-reload behavior in your own deployment before relying on
it around hardware.

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
