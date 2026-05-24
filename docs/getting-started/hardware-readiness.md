# Hardware Readiness

Use this checklist before running DAM against physical robot hardware. It is intentionally practical: confirm the environment, validate the Stackfile, then move from monitor-style learning to enforce-style operation only when the signals make sense.

## Before Connecting Hardware

- You completed [Quick Start](quickstart.md) without hardware.
- `make validate` passes for the example Stackfiles.
- You can explain the task name and active boundaries in your hardware Stackfile.
- You know how to stop the robot outside DAM.
- The robot workspace is clear and the physical stop procedure has been tested.

## Check The Hardware Stackfile

For the SO-ARM101 example:

```bash
.venv/bin/dam inspect examples/stackfiles/test.yaml
```

Confirm:

| Field | What to verify |
|-------|----------------|
| `hardware.sources.arm.port` | Matches the connected serial device |
| Camera sources | `index_or_path` values match local cameras |
| `policy.device` | Uses a device supported by your machine |
| `safety.enforcement_mode` | Starts from the mode you intend |
| `tasks.soarm101.boundaries` | Contains only boundaries you want active |

## Run In Small Steps

1. Validate: `.venv/bin/dam validate examples/stackfiles/test.yaml`
2. Inspect: `.venv/bin/dam inspect examples/stackfiles/test.yaml`
3. Start with a short controlled run:
   ```bash
   .venv/bin/dam run examples/stackfiles/test.yaml --cycles 50 --task soarm101
   ```
4. Watch the console for guard decisions and latency.
5. Stop and review any reject, fault, repeated clamp, or latency warning.

## What To Watch In The Console

| Signal | Why it matters |
|--------|----------------|
| Guard status | Shows which layer is making decisions |
| Event log | Shows recent clamp, reject, fault, and control events |
| Risk gauge | Summarizes current risk state |
| Cycle latency | Shows whether the loop is staying inside budget |
| MCAP Sessions | Lets you inspect safety events after the run |

## Do Not Continue If

- The Stackfile does not validate.
- The selected task name is wrong.
- Hardware health boundaries reject immediately and you do not know why.
- Console latency is repeatedly over budget.
- You cannot stop the robot independently of DAM.

Return to [Troubleshooting](troubleshooting.md) or [Console Walkthrough](console-walkthrough.md) before continuing.
