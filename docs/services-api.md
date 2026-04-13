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
