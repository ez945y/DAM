# Failure Tuple Schema

DAM records failure-harvesting fields on `/dam/cycle` so experiments can separate perception anomalies from risky actions and physical hardware risk without re-parsing every guard result offline.

## Failure classes

Classification is defined **purely by guard layer and fault source** — guard
names are never inspected. The single source of truth is
`dam.runtime.failure_classify.classify_failure`, used by both the runtime
harvester and the RQ5 experiment runner.

| Category | `failure_type` | Classification rule |
|---|---|---|
| 感知異常事件 — perception anomaly | `ood_only` | Only L0 perception-anomaly guards fired, and nothing else. |
| 動作風險事件 — action risk | `guard_triggered` | Any non-hardware guard rejected, limited, or fault-arbitrated an action (e.g. an L0 detector together with an action guard). |
| 硬體風險事件 — hardware risk | `hardware_triggered` | Any L3 guard, or any guard reporting `fault_source="hardware"`, fired. Highest priority. |

Priority order is:

```text
hardware_triggered > guard_triggered > ood_only
```

The `host_health` L3 boundary (host CPU/GPU/memory/temperature) reports
`fault_source="hardware"`, so a computer-side breach is classified as a
硬體風險事件 (`hardware_triggered`) automatically.

## MCAP fields

The `/dam/cycle` payload includes both compact columns and the full tuple:

| Field | Type | Description |
|---|---|---|
| `failure_type` | string or null | One of `ood_only`, `guard_triggered`, `hardware_triggered`, or null when no failure-worthy outcome occurred. |
| `failure_guard_names` | string[] | Guards that produced REJECT, CLAMP, or FAULT. |
| `failure_layers` | string[] | Layer labels for those guards, for example `["L0", "L1"]`. |
| `failure_decisions` | string[] | Decision names aligned with `failure_guard_names`. |
| `failure_reasons` | string[] | Guard reason strings aligned with `failure_guard_names`. |
| `failure_tuple` | object or null | Structured evidence object for paper/export use. |

## Tuple schema

For a control cycle `t`, DAM emits:

```text
F_t = <id, trace, time, class, task, boundaries, guards, layers, decisions, reasons, masks, action, validated_action, fallback, observation_channels>
```

JSON representation:

```json
{
  "schema": "dam.failure_tuple.v1",
  "cycle_id": 42,
  "trace_id": "8f5e...",
  "timestamp": 12345.67,
  "failure_type": "guard_triggered",
  "active_task": "pick_place",
  "active_boundaries": ["ood_detector", "joint_position_limits"],
  "guard_names": ["motion"],
  "layers": ["L1"],
  "decisions": ["REJECT"],
  "reasons": ["joint 2 above upper limit"],
  "fault_sources": [null],
  "has_violation": true,
  "has_clamp": false,
  "violated_layer_mask": 2,
  "clamped_layer_mask": 0,
  "fallback_triggered": "emergency_stop",
  "action_target_positions": [0.0, 0.1],
  "validated_positions": null,
  "observation_channels": ["current", "joint_velocities"]
}
```

This tuple is intentionally evidence-oriented. It keeps raw guard names and reasons available so a paper can report class-level rates while still allowing audits of the exact detector or boundary that fired.
