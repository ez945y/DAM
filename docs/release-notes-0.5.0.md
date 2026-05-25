# DAM 0.5.0 Release Notes

DAM 0.5.0 turns the thesis evaluation runners into first-class, real
measurements, adds guard-checked dataset playback to physical hardware, and
makes the failure-event taxonomy a shared, spec-aligned classifier. The
console gains a native Experiments workspace plus useful live/recorded robot
hardware and multi-camera inspection.

## Guarded Dataset Replay

- A production `dataset` adapter can now provide recorded observations and
  actions while a real motor sink receives only guard-validated commands.
  The implementation reuses the normal runtime, sink, safety pipeline, camera
  hub, live preview, and MCAP recorder rather than creating a replay-only
  execution path.
- `dataset_replay_check.yaml` is the focused hardware-validation example:
  recorded actions, real SO-101 output, and top/wrist live camera recording.
- Dataset-to-hardware replay runs in strict mode: absent recorded actions fail
  closed instead of falling back to synthetic actions.

## Guard Configuration and Inspection

- Risk Log and Cycle Inspector now use one shared cycle I/O payload and render
  a merged per-joint table for state, proposed target, validated output, and
  motor temperature/current/voltage where available.
- Risk outcomes are canonical (`reject`, `clamp`, `pass`); filtering a
  fallback-resolved guard rejection now still finds the rejected cycle.
- Templates emit boundaries in layer order (`L0` through `L3`) and include
  L1-L3 coverage. L2/L3 presets expose `warn_frames`, the existing
  consecutive-cycle reaction threshold, instead of hiding transient filtering.
- The nominal SO-101 voltage safety band is corrected to `10.0-13.0 V` for
  a 12 V supply. The previous `6.0-8.5 V` example values could trigger an
  immediate stop on normally powered hardware.
- Task gripper workflow nodes no longer use a one-second timeout as if it
  measured callback latency; these phases advance explicitly with the task.

## Experiment Runners

- RQ1–RQ5 are exposed as native runners (`dam.experiments`) usable from the
  console, the `dam experiment` CLI, and `POST /api/experiments/{id}/run`.
  Each run writes `results.csv` plus an SVG (and PNG where applicable)
  artifact, served by `GET /api/experiments/artifact`. RQ1 uses a PNG preview
  because NLL values can be negative and are not represented correctly by the
  earlier SVG median-bar preview.
- **RQ3 (Normal-Use False Trigger Study)** and **RQ5 (Failure Record
  Quality)** are now *real measurements*. They were placeholder constants;
  they now drive the real L0–L2/L3 guard stack:
    - RQ3 (`scripts/run_usability_study.py`) feeds benign, in-limit frames
      across legal deployment variations and counts genuine false triggers
      from real `guard.check()` decisions.
    - RQ5 (`scripts/run_record_quality.py`) drives real violating scenarios
      across all three event categories, harvests records through the shared
      production classifier, and scores completeness, classification, layer
      labels, readable reasons, observation window, and taxonomy coverage.
- RQ2 (`run_boundary_scan.py`) remains a real guard-driven sweep. RQ4 now
  performs isolated Guard profiling for the thesis latency study: 10 Hz,
  20 Hz, and 50 Hz are launched sequentially from the console, each comparing
  No Safety, Rule-based Safety, OOD-only, and Full RSMF over 500 time steps.
  Console runs use short visual pacing by default, with a full wall-clock
  `realtime=true` option, then embed the plot directly in the result card. The
  measurement window starts at action proposal receipt and ends at the
  validated action decision, excluding image preprocessing and policy
  inference. RQ1 (L0 calibration) is an offline evaluation harness over the
  shared DAM L0 feature and OOD backend APIs: it trains Real-NVP on normal
  SO-ARM observations, then compares per-frame NLL across normal,
  legal-variation, and abnormal-A HuggingFace datasets. RQ1 can optionally
  compare Welford, MemoryBank, and Real-NVP scoring on the same features, and
  can fuse pretrained image embeddings when a vision model is selected.

## Event Taxonomy

- Classification now lives in one place — `dam.runtime.failure_classify` —
  and is used by both the runtime harvester and RQ5.
- Categories are defined purely by guard **layer** and **fault source**; the
  fragile guard-name substring heuristics (`"ood"` / `"hardware"` in the
  name) were removed. Behaviour for the built-in guards is unchanged.

| Category | `failure_type` | Rule |
|---|---|---|
| 感知異常事件 — perception anomaly | `ood_only` | Only L0 perception-anomaly guards fired, and nothing else. |
| 動作風險事件 — action risk | `guard_triggered` | Any non-hardware guard rejected, limited, or fault-arbitrated an action. |
| 硬體風險事件 — hardware risk | `hardware_triggered` | Any L3 guard, or any guard with a hardware fault source, fired (highest priority). |

## Host Health Boundary

- The `host_health_limit` L3 boundary samples host CPU / GPU / memory /
  temperature (psutil + load average + `nvidia-smi`) and faults with
  `fault_source="hardware"` — so a computer-side breach is a 硬體風險事件
  automatically.
- Wired into the canonical Console template, `examples/stackfiles/demo.yaml`,
  `so101.yaml`, and the default local config.
- Fixed a crash: `host_health_limit` returned `layer="L3"` as a string,
  which broke failure harvesting (`int(r.layer)`) on every host-health fault.
  It now uses the `GuardLayer` enum, and the classifier tolerates
  enum/int/`"L3"` layer forms.
- `nvidia-smi` rows reporting `[N/A]` no longer raise; those GPUs are skipped.

## MCAP & Console

- The MCAP player and live stream can display multiple cameras from the shared
  camera hub; hardware replay templates capture both top and wrist streams.
- Guard `metadata` (including `host_health`) is now surfaced through the
  positional Rust cycle path, not only the dict path, so the MCAP inspector
  shows it for Rust-recorded sessions too.
- Cycle / observation / action detail use flexible fields instead of a fixed
  column set; the inspector renders guard metadata and the failure record.
- Replay/session review is less brittle: empty session and empty camera states
  keep the same framed inspector/player layout, selected-cycle details stay
  compact, and session deletion now archives MCAP files into `_trash` instead
  of unlinking them immediately.
- The live camera path is hub-owned end to end. Runtime/adapters publish JPEG
  frames to the image hub, telemetry sends the camera names in JSON plus JPEG
  bytes as binary WebSocket payloads, and the legacy `/api/mcap/live` /
  `live_images` JSON preview path was removed.
- Dashboard "Cycle Latency" panel gains a **Latency / Hardware** tab. The
  Hardware tab subscribes to host + robot health from telemetry and stays
  calm/idle when no task is running or no health-reporting guard is active.
- Fixed the empty "History (last 60 cycles)" chart after a simulated
  Stop→Start or episode wrap: the session-scoped latency window now resets
  and refreshes instead of being blocked by stale cycle ids.

## Breaking Changes

- `version.toml` was removed. The version is now read from
  `pyproject.toml`; `scripts/sync_version.py` reads the same source.
- Failure classification no longer inspects guard names. Custom guards that
  relied on a name containing `ood`/`hardware` for classification must use
  the correct layer or set `fault_source="hardware"`.
- Built-in SO-101 templates use `10.0-13.0 V` as the nominal voltage band.
  Deployments intentionally powered outside that band must set their own
  `voltage_limit` parameters.
