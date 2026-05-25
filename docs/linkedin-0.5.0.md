# LinkedIn Post Draft: DAM 0.5.0

I have been building DAM (Detachable Action Monitor) to make robot policy
execution easier to inspect and safer to deploy.

In v0.5.0, a recorded dataset can be replayed through the same guard pipeline
and then sent to a real robot only after validation. The live console and MCAP
recording capture multiple cameras plus readable joint and motor telemetry, so
a safety decision is reviewable rather than a black box.

The core design is lifecycle-aware: L0 checks unfamiliar observations before
action, L1 constrains physical motion, L2 checks task-phase intent over
consecutive cycles, and L3 watches hardware and host health during execution
and fallback. Each layer shares the same runtime, event log, and replay path.

Highlights:

- guarded dataset-to-hardware replay for SO-101
- merged joint/telemetry inspection in live and recorded sessions
- multi-camera live preview and MCAP playback
- configurable consecutive-cycle reactions for task and hardware anomalies
- real experiment runners for evaluating safety behavior

This release made an important principle concrete for me: a robotics safety
feature is only useful when the operator can understand why it acted.
