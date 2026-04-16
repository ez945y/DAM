# Loopback Logging — MCAP Session Archive

Loopback logging captures a continuous stream of cycle records (observations, actions, guard results) in [MCAP](https://mcap.dev/) format for post-mortem analysis and live playback.

---

## Overview

When a guard **rejects** or **faults**, the LoopbackWriter automatically captures ±10 seconds of sensor observations (including camera frames) alongside the decision context. All cycles (pass/clamp/reject) are recorded in a single time-indexed archive per runtime session.

### Key features

- **Non-blocking**: Records written to an async queue; main control loop adds < 20 µs overhead
- **Structured format**: MCAP with JSON schemas for each channel type
- **Violation context**: Ring buffer of observations ±10 seconds around any rejection
- **Image capture**: Optional on clamps; always on violations (if camera present)
- **Rotation**: Automatic file rotation every 500 MB or 60 minutes
- **Compression**: MCAP chunk-level zstd compression (~70% reduction)

---

## Configuration

Enable in Stackfile under `loopback:`:

```yaml
loopback:
  backend: mcap                    # "mcap" (recommended) or "pickle"
  output_dir: /data/robot/sessions # Directory for session files
  window_sec: 10.0                 # Total duration (pre + post) around an event to keep images
  pre_event_sec: 10.0             # How many seconds of history to capture before a violation
  rotate_mb: 500.0                 # Rotate file every 500 MB
  rotate_minutes: 60.0             # Or rotate every 60 minutes
  max_queue_depth: 256             # Records queued before dropping
  capture_images_on_clamp: false   # Also capture images on CLAMP?
```

### Tuning

| Parameter | Default | Guidance |
|-----------|---------|----------|
| `window_sec` | 10.0 | Total image sequence duration captured around an event. |
| `pre_event_sec` | 10.0 | Specific amount of historical images to pull from the ring buffer before the trigger. |
| `rotate_mb` | 500.0 | Reduce to 100–200 MB if disk space is tight. |
| `rotate_minutes` | 60.0 | Rotation period; 60 min is standard for debugging. |
| `capture_images_on_clamp` | false | Enable to debug motion limit triggers; can be disk-intensive. |
| `max_queue_depth` | 256 | Increase if you see warnings about queue full on a slow storage backend |

---

## MCAP Channels & Schema

Each session is written as a single `.mcap` file with the following channels:

### `/dam/cycle` — Control loop summary

Written every cycle. Summarises the decision and latency snapshot.

```json
{
  "cycle_id": 42,
  "trace_id": "3fa80...",
  "timestamp": 1700000000.123,
  "active_task": "move_tcp",
  "active_boundaries": ["workspace_check", "speed_limit"],
  "active_cameras": ["top", "wrist"],
  "has_violation": false,
  "has_clamp": false,
  "violated_layer_mask": 0,
  "clamped_layer_mask": 0,
  "source_ms": 0.8,
  "policy_ms": 2.1,
  "guards_ms": 5.4,
  "sink_ms": 0.4,
  "total_ms": 8.7
}
```

**Fields:**
- `has_violation`: true if any guard **rejected** or **faulted** this cycle
- `violated_layer_mask`: Bitmask (bit i = Layer i had a violation); used to quickly filter MCAP
- `has_clamp`: true if any guard **clamped** (and `capture_images_on_clamp=true`)
- `clamped_layer_mask`: Bitmask for clamps
- `latency_*`: Pipeline timings (source, policy, guards, sink) from `MetricBus`

### `/dam/obs` — Sensor observation

Raw joint state, EE pose, force/torque. One message per cycle.

```json
{
  "cycle_id": 42,
  "timestamp": 1700000000.123,
  "joint_positions": [0.0, 1.57, -1.57, 0.0, 0.0, 0.0],
  "joint_velocities": [0.01, 0.02, -0.01, 0.0, 0.0, 0.0],
  "end_effector_pose": [0.5, 0.3, 0.2, 1.0, 0.0, 0.0, 0.0],
  "force_torque": [5.0, -2.0, 20.0, 0.1, 0.05, 0.02]
}
```

### `/dam/action` — Proposed and validated action

Command trajectory before and after guard processing.

```json
{
  "cycle_id": 42,
  "timestamp": 1700000000.123,
  "proposal_positions": [0.0, 1.6, -1.5, 0.0, 0.0, 0.0],
  "proposal_velocities": [0.01, 0.02, -0.01, 0.0, 0.0, 0.0],
  "validated_positions": [0.0, 1.57, -1.57, 0.0, 0.0, 0.0],
  "validated_velocities": [0.005, 0.015, -0.01, 0.0, 0.0, 0.0],
  "was_clamped": false,
  "was_rejected": false,
  "fallback_triggered": null
}
```

**Note:** If `was_rejected=true`, then `validated_*` are `null` (action did not execute).

### `/dam/L0` … `/dam/L4` — Per-layer guard results

One message per guard per cycle (only if guard is active).

```json
{
  "cycle_id": 42,
  "timestamp": 1700000000.123,
  "guard_name": "OODGuard",
  "boundary": "obs_check",
  "decision": "PASS",
  "is_violation": false,
  "is_clamp": false,
  "reason": "",
  "latency_ms": 2.1
}
```

**Decision values:** `PASS` | `CLAMP` | `REJECT` | `FAULT`

Use `is_violation=true` to filter rejection-only analysis; use `is_clamp=true` to filter clamp-only.

### `/dam/images/{cam_name}` — Camera frame

JPEG-encoded image from sensor, captured only when `has_violation=true` (or on clamps if `capture_images_on_clamp=true`).

```json
{
  "cycle_id": 42,
  "timestamp": 1700000000.123,
  "jpeg_base64": "..."
}
```

**Frequency:** May be sparse if violations are rare. Use `cycle_id` to correlate with `/dam/obs`.

### `/dam/latency` — Per-layer latency aggregates

Aggregate latency per layer, written every cycle (requires `MetricBus`).

```json
{
  "cycle_id": 42,
  "timestamp": 1700000000.123,
  "L0_ms": 2.1,
  "L1_ms": 0.5,
  "L2_ms": 1.9,
  "L3_ms": 0.3,
  "L4_ms": 0.6
}
```

---

## Session Metadata

Each `.mcap` file contains session-level metadata (written once at start):

```json
{
  "session": {
    "session_id": "sess_20241210_143022_abc123",
    "dam_version": "1.5.0",
    "control_frequency_hz": 50.0,
    "python_version": "3.12.0",
    "stackfile_path": "/config/robot.yaml",
    "stackfile_hash": "sha256:abc123...",
    "timestamp": 1700000000.123
  }
}
```

---

## Reading & Playback

### Python: mcap-reader

```bash
pip install mcap[numpy]
```

```python
from mcap.reader import McapReader

with open("session_20241210_143022.mcap", "rb") as f:
    reader = McapReader(f)
    
    # List all channels
    for channel in reader.channels.values():
        print(f"/{channel.topic}: {channel.message_encoding}")
    
    # Read violation cycles
    messages = reader.get_messages(topics=["/dam/cycle"])
    for msg in messages:
        cycle = json.loads(msg.message.data)
        if cycle["has_violation"]:
            print(f"Violation at cycle {cycle['cycle_id']}: {cycle['violated_layer_mask']}")
```

### Web Console

(Planned) Open any `.mcap` session in the **MCAP Viewer** page:

1. Navigate to **Console** → **MCAP Sessions**
2. Choose a session file
3. View:
   - Timeline of all cycles (pass / clamp / reject / fault)
   - Images side-by-side with guard decisions
   - Latency graph per layer
   - Export filtered subset as CSV / JSON

---

## API Endpoints

### `GET /mcap/sessions`

List all session files in `output_dir`.

```bash
curl http://localhost:8080/mcap/sessions
```

Response:

```json
{
  "sessions": [
    {
      "session_id": "sess_20241210_143022_abc123",
      "filename": "session_20241210_143022_abc123.mcap",
      "size_mb": 123.4,
      "created_at": 1700000000.123,
      "rotated_at": 1700003600.456,
      "file_count": 3,
      "violation_count": 5,
      "clamp_count": 12
    }
  ]
}
```

### `GET /mcap/sessions/{session_id}`

Metadata for a specific session (parse headers without reading full file).

```bash
curl http://localhost:8080/mcap/sessions/sess_20241210_143022_abc123
```

Response:

```json
{
  "session_id": "sess_20241210_143022_abc123",
  "start_time": 1700000000.123,
  "end_time": 1700003600.789,
  "total_cycles": 180000,
  "violation_cycles": 5,
  "clamp_cycles": 12,
  "has_images": true,
  "compression": "zstd",
  "channels": ["/dam/cycle", "/dam/obs", "/dam/action", "/dam/L0", "/dam/L2", "/dam/images/camera0"]
}
```

### `GET /mcap/sessions/{session_id}/download?start_cycle=0&end_cycle=1000&topics=/dam/cycle,/dam/obs`

Download a filtered subset of the session (useful for sharing specific incidents).

```bash
# Download 100 cycles starting from cycle 0, only /dam/cycle and /dam/obs
curl 'http://localhost:8080/mcap/sessions/sess_20241210_143022_abc123/download?start_cycle=0&end_cycle=100&topics=/dam/cycle,/dam/obs' \
  -o incident_subset.mcap
```

---

## Troubleshooting

### Queue full warnings

```
[WARNING] LoopbackWriter: queue full (256 slots), dropping cycle 742
```

**Cause:** Writer thread cannot keep up with record rate. Occurs when:
- Slow storage (rotating disk, network mount)
- Large images (high resolution, many cameras)
- High guard count (many channels to write per cycle)

**Fixes:**
1. Increase `max_queue_depth` to 512 or 1024
2. Reduce `window_sec` (fewer images per violation)
3. Set `capture_images_on_clamp: false`
4. Enable MCAP compression (automatic; check `rotate_mb`)

### High latency spikes

Check `/dam/latency` channel for which layer is slow. If it's a guard:

```python
for msg in reader.get_messages(topics=["/dam/L2"]):
    guard = json.loads(msg.message.data)
    if guard["latency_ms"] > 10:
        print(f"{guard['guard_name']}: {guard['latency_ms']:.2f} ms")
```

Common culprits:
- **L0 (OOD)**: Model inference slow → reduce model size or batch smaller
- **L1 (Preflight)**: Physics sim slow → increase time budget in stackfile
- **L2 (Motion)**: Large numpy operations → profile with `cProfile`

### Writer thread crashed

If writer crashes, records stop being written but main loop continues (graceful degradation). Check logs:

```bash
grep "LoopbackWriter.*ERROR" /var/log/dam.log
```

Common causes:
- Permission denied on `output_dir`
- Disk full
- Corrupted MCAP schema cache

**Recovery:** Restart the runtime; a new session file will be created.

---

## Best Practices

1. **Size for your storage**: Estimate 10–50 MB/hour at 50 Hz with 1–2 cameras (depends on image resolution).
   - For 8 hours: ~80–400 MB
   - For 24 hours: ~240–1200 MB
   - Adjust `rotate_mb` accordingly

2. **Separate violation & operation logs**:
   - Violations → low `max_queue_depth` (64–128), high `capture_images_on_clamp` to catch context
   - Long-running ops → `capture_on_violation=false` + periodic manual snapshots

3. **Offline analysis**: Download sessions to a local machine and use `mcap-cli` or Python reader:
   ```bash
   mcap dump session_20241210.mcap | jq '.message | select(.topic == "/dam/cycle" and .payload.has_violation)'
   ```

4. **Correlate with logs**: Use `trace_id` (same in MCAP and risk-log API) to match console events with file records.

---

## See also

- [Services API → Loopback endpoints](services-api.md#loopback)
- [Stackfile Guide → loopback section](quick-stack.md#loopback)
- [MCAP Spec](https://mcap.dev/spec/)
