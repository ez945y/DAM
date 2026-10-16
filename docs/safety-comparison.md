# Safety A/B comparison and regression cases

Open **Replay → Safety A/B comparison & regression cases**. Select a completed MCAP
recording and two entries from the Stackfile library (or the live configuration
snapshot). Both candidates evaluate every recorded input in cycle order with
separate runtime histories. Acceleration callback caches use a fresh private scope
for each candidate and regression run; live-runtime cache entries are never reset.
The result includes all cycles, decision distributions,
changed outputs, correction norms, the largest differences, and individual errors.
`FAULT` remains distinct from `REJECT`.

This is an offline comparison of guard decisions and validated commands. It does
not send commands, run fallback actions, or predict the trajectory the robot would
follow after a changed command. Candidate guards run synchronously for each cycle,
including guards configured for a slow lane; worker timing, stale verdicts and
startup waits are not reproduced. Snapshots retain the original configuration.

## Inputs and limits

The evaluator reads current packed `/dam/cycle` records, historical positional
msgpack cycle records, and explicit `/dam/obs` plus `/dam/action` records. A missing
proposed action, timestamp, task, required observation, or available offline solver
is reported. Missing values are never replaced with zeros. A missing/invalid cycle
invalidates the remaining candidate history rather than silently skipping it.

Supported callbacks include joint position, velocity and acceleration limits;
workspace, keep-out, orientation and end-effector velocity limits when an offline
dynamics solver is available; temperature, current, voltage and force/torque
limits with recorded channels; and single-node task workspace/speed limits.
List/graph container transitions, image-dependent guards, host-health checks,
watchdog age and custom callbacks currently return `unsupported` because the
necessary state or a supported faithful replay contract is unavailable. In
particular, the recording carries an observation timestamp but not the original
control-loop timestamp needed to recompute sensor age.

Requests are limited to 64 MiB and 20,000 cycles, with a 60-second evaluation
budget per candidate checked between cycles. One comparison/regression evaluation
runs at a time. The last eight comparisons are cached until service restart.

## Executable regressions

Choose a candidate, cycle range and case name. Keep **Match current decisions &
outputs**, or choose an explicit **Must be PASS/CLAMP/REJECT/FAULT** requirement.
An explicit requirement applies to every cycle in that range. A regression is
saved with the MCAP SHA-256 fingerprint, the full configuration snapshot and hash,
and expected outcomes. Only fully evaluated ranges may be saved.

**Rerun case** verifies both fingerprints and reruns the saved candidate. It replays
all preceding cycles before checking the selected range, preserving command history.
It returns `pass`, `fail` with expected/actual differences, or `error` for missing
inputs, a changed recording or an unsupported evaluation. Output matching uses
absolute tolerance `1e-8` in the recorded action units, with no relative tolerance.
Files are saved under `data/safety_regressions/` and survive service restarts.

## Runnable example

```bash
.venv/bin/python -m examples.safety_comparison_demo
```

This creates a temporary five-cycle MCAP and two configurations. The roomy
configuration produces five PASS outcomes; the precise configuration produces
three PASS and two CLAMP outcomes. The example then reruns an assertion requiring
the two out-of-range proposals to be clamped.

To install these samples in the console library and default session directory:

```bash
.venv/bin/python -m examples.safety_comparison_demo --install-for-console
```

The command refuses to overwrite existing sample files. Refresh the Replay choices
and select `session_safety_comparison_demo.mcap`, `comparison-roomy` and
`comparison-precise`. If the service uses a custom sessions directory, place the
sample recording there.

## API

- `POST /api/safety/comparisons`: `{mcap, left_stack, right_stack}`. Stack names
  reference the library; `__live__` snapshots the current live file. Returns the
  complete comparison with an `id`, config snapshots, distributions, all cycles,
  differences, top differences, limitations and status.
- `GET /api/safety/comparisons/{id}`: retrieve a retained result.
- `POST /api/safety/regressions`: `{comparison_id, name, side, cycle_start,
  cycle_end, expected_decision?}`; `side` is `left` or `right`.
- `GET /api/safety/regressions`: list durable cases and any unreadable-file errors.
- `POST /api/safety/regressions/{id}/run`: execute the saved assertions.

Recording resolution uses the existing MCAP session service's path checks; arbitrary
filesystem recording or Stackfile paths are not accepted.
