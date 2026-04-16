# Services API Reference

The DAM API server exposes REST endpoints and a WebSocket stream.

Start the server:

```bash
uvicorn dam.services.api:app --host 0.0.0.0 --port 8080
# Interactive docs: http://localhost:8080/docs
```

---

## Telemetry

### `GET /api/telemetry/history`

Return the last *n* cycle events from the ring buffer.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `n` | int | 50 | Number of events (1–1000) |

### `WS /ws/telemetry`

Real-time WebSocket stream. Replays last 50 events on connect,
then pushes every new `CycleResult` as it arrives.

#### Wiring `TelemetryService` with `MetricBus`

Pass the runtime's `MetricBus` and the cycle budget when constructing
`TelemetryService` to enable the `perf` field on every event:

```python
from dam.services.telemetry import TelemetryService

telemetry = TelemetryService(
    metric_bus=runtime.metric_bus,
    cycle_budget_ms=1000.0 / runtime.control_frequency_hz,
)
```

When `metric_bus` is omitted (the default), the service behaves exactly as
before and the `perf` key is absent from all events.

#### Event shape

Each message is a JSON object with `"type": "cycle"`.  
The `perf` field is present only when `MetricBus` is wired in (see above).

```json
{
  "type": "cycle",
  "cycle_id": 42,
  "trace_id": "3fa8b1c2-...",
  "was_rejected": false,
  "was_clamped": false,
  "risk_level": "NORMAL",
  "fallback_triggered": null,
  "latency_ms": {
    "obs": 0.8,
    "policy": 2.1,
    "validate": 5.4,
    "sink": 0.4,
    "total": 8.7
  },
  "guard_statuses": [
    { "name": "OODGuard",     "layer": "L0", "decision": "PASS", "reason": "" },
    { "name": "MotionGuard",  "layer": "L2", "decision": "PASS", "reason": "" }
  ],
  "active_task": "pick",
  "active_boundaries": ["workspace", "approach"],
  "active_cameras": ["top", "wrist"],
  "timestamp": 1700000000.123,

  "perf": {
    "stages": {
      "source":  0.8,
      "policy":  2.1,
      "guards":  5.4,
      "sink":    0.4,
      "total":   8.7
    },
    "layers": {
      "L0": 2.1,
      "L2": 2.0,
      "L4": 1.3
    },
    "guards": {
      "OODGuard":    2.1,
      "MotionGuard": 1.0,
      "WorkspaceGuard": 1.0,
      "HardwareGuard": 1.3
    },
    "deadline_ms": 20.0,
    "slack_ms": 11.3
  }
}
```

| `perf` field | Type | Description |
|---|---|---|
| `stages.source` | float ms | Sensor read (`source.read()`) |
| `stages.policy` | float ms | Policy inference (`policy.predict()`) |
| `stages.guards` | float ms | All guards combined (`validate()`) |
| `stages.sink` | float ms | Action dispatch (`sink.apply()`) |
| `stages.total` | float ms | End-to-end cycle time |
| `layers.*` | float ms | Sum of guard latencies for that `GuardLayer` in this cycle.<br>Keys follow the `"L0"`–`"L4"` convention; only layers that executed appear. |
| `guards.*` | float ms | Per-guard latest execution time. |
| `deadline_ms` | float ms | Configured cycle budget (`1000 / control_frequency_hz`). |
| `slack_ms` | float ms | Remaining headroom: `deadline_ms − total_ms`. Negative means over-budget. |

#### Binary Message Protocol

To avoid Base64 encoding overhead (which adds ~33% bandwidth and significant CPU latency), live camera frames are pushed as **raw binary messages** over the same WebSocket connection immediately following a cycle JSON event.

Connected clients should set `ws.binaryType = "arraybuffer"` and parse binary messages using the following protocol:

| Byte Offset | Size | Name | Description |
|---|---|---|---|
| **0** | 1 byte | **Magic** | Protocol version identifier. Fixed: `0x01` |
| **1** | 1 byte | **Name Length** (*L*) | Length of the camera name string in bytes. |
| **2** | *L* bytes | **Camera Name** | UTF-8 encoded camera name (e.g., `top`). |
| **2 + L** | variable | **JPEG Payload** | Raw binary JPEG data. |

Frontends can render these efficiently using `URL.createObjectURL(new Blob([jpegData], {type: 'image/jpeg'}))`.

A `{"type":"ping"}` keepalive is sent every 30 s.

---

## Risk Log

### `GET /api/risk-log`

Query historical risk events.

| Parameter | Type | Description |
|-----------|------|-------------|
| `since` | float | Unix timestamp lower bound |
| `until` | float | Unix timestamp upper bound |
| `min_risk_level` | str | `NORMAL` · `ELEVATED` · `CRITICAL` · `EMERGENCY` |
| `rejected_only` | bool | Return only rejected cycles |
| `clamped_only` | bool | Return only clamped cycles |
| `limit` | int | Max events (default 100, max 5000) |

### `GET /api/risk-log/stats`

Summary statistics: total events, rejected, clamped, by risk level, avg latency.

### `GET /api/risk-log/export/json`

Download all events as `risk_log.json`.

### `GET /api/risk-log/export/csv`

Download all events as `risk_log.csv`.

### `GET /api/risk-log/{event_id}`

Get a single event by its integer ID.

---

## Boundaries

### `GET /api/boundaries`
List all boundary configs.

### `GET /api/boundaries/{name}`
Get a single boundary config.

### `POST /api/boundaries`
Create a new boundary config. Body: boundary config JSON.

### `PUT /api/boundaries/{name}`
Replace a boundary config.

### `DELETE /api/boundaries/{name}`
Delete a boundary config.

---

## Runtime Control

### `GET /api/control/status`

```json
{
  "state": "running",
  "cycle_count": 1234,
  "error": null,
  "has_runtime": true
}
```

States: `idle` · `running` · `paused` · `stopped` · `emergency`

### `POST /api/control/start`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task_name` | str | `default` | Task name to activate |
| `n_cycles` | int | -1 | Cycles to run (-1 = run forever) |
| `cycle_budget_ms` | float | 20.0 | Target cycle time in ms |

### `POST /api/control/pause`
Pause after the current cycle completes.

### `POST /api/control/resume`
Resume a paused runtime.

### `POST /api/control/stop`
Graceful stop.

### `POST /api/control/estop`
Immediate emergency stop. Also calls `sink.emergency_stop()` if available.

### `POST /api/control/reset`
Reset to `idle` (only from `stopped` or `emergency`).

---

## Loopback Sessions

!!! note
    These endpoints are available only if `loopback.backend = "mcap"` is set in the Stackfile
    and at least one cycle has been written.

### `GET /mcap/sessions`

List all MCAP session files in the configured `output_dir`.

**Response:**

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
      "total_cycles": 180000,
      "violation_cycles": 5,
      "clamp_cycles": 12,
      "has_images": true
    }
  ]
}
```

### `GET /mcap/sessions/{session_id}`

Metadata for a specific session (MCAP header only; does not read entire file).

**Response:**

```json
{
  "session_id": "sess_20241210_143022_abc123",
  "filename": "session_20241210_143022_abc123.mcap",
  "size_mb": 123.4,
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

### `GET /mcap/sessions/{session_id}/download`

Download (a subset of) the session file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start_cycle` | int | 0 | First cycle to include |
| `end_cycle` | int | -1 | Last cycle to include (-1 = all) |
| `topics` | str | (all) | Comma-separated channel list (e.g. `/dam/cycle,/dam/L2`) |

**Example:**

```bash
# Download cycles 100–200, only /dam/cycle and /dam/L2
curl 'http://localhost:8080/mcap/sessions/sess_20241210.../download?start_cycle=100&end_cycle=200&topics=/dam/cycle,/dam/L2' \
  -o incident.mcap
```

**Response:** Binary MCAP file with Content-Disposition attachment.

---
