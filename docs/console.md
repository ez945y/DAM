# DAM Console

The DAM Console is a real-time web dashboard for monitoring and controlling the DAM runtime.
It is built with **Next.js 14**, **TypeScript**, and **Tailwind CSS**, and connects to the
DAM API server via REST and WebSocket.

---

## Quick start

=== "Docker (recommended)"

    ```bash
    # Start API + Console together
    docker compose up api console-dev

    # → Console: http://localhost:3000
    # → API docs: http://localhost:8080/docs
    ```

=== "Local dev"

    ```bash
    # Terminal 1 — API server
    pip install "dam[dev,services]"
    uvicorn dam.services.api:app --host 0.0.0.0 --port 8080 --reload

    # Terminal 2 — Console
    cd dam-console
    npm install
    npm run dev
    # → http://localhost:3000
    ```

---

## Pages

### Dashboard `/`

Real-time overview of the running DAM runtime.

| Widget | Description |
|--------|-------------|
| **Risk Gauge** | SVG arc gauge showing current risk level (NORMAL → EMERGENCY) |
| **Stats cards** | Total cycles, reject count + rate, clamp count, average latency |
| **Runtime Control** | Start / Pause / Resume / Stop / E-STOP / Reset |
| **Cycle Latency** | Rolling area chart of the last 60 cycle latencies |
| **Guard Status** | Per-guard table: name, layer, last decision, reason |
| **Event Log** | Filterable scrolling event log with timestamps |

### Config `/config`

Visual Stackfile editor. Pick a template, configure adapters, manage USB devices,
edit joint limits, then download the generated YAML.

| Section | Description |
|---------|-------------|
| **Template Gallery** | SO-101 ACT, SO-101 Diffusion, ROS2 Minimal, Simulation |
| **Adapter Picker** | Source (sensor) · Policy (brain) · Sink (actuator) |
| **USB Devices** | Add / remove host → container device paths |
| **Joint Limits** | Per-joint lower/upper limits and max velocity table |
| **YAML Editor** | Live-generated Stackfile YAML with copy/download/load |

### Risk Log `/risk-log`

Historical risk event table with filters and export.

```
Filter by: risk level · rejected-only · clamped-only · time range
Export: JSON download · CSV download
```

### Boundaries `/boundaries`

CRUD interface for the in-memory BoundaryConfigService.

!!! note
    Changes made here update only the **in-memory** config store.
    For persistent configuration use a Stackfile.

---

## WebSocket stream

The console subscribes to `ws://<host>:8080/ws/telemetry`.
Each cycle the API pushes a JSON message:

```json
{
  "type": "cycle",
  "cycle_id": 42,
  "trace_id": "3fa8...",
  "was_rejected": false,
  "was_clamped": false,
  "risk_level": "NORMAL",
  "fallback_triggered": null,
  "latency_ms": { "obs": 0.8, "policy": 2.1, "validate": 1.3, "sink": 0.4, "total": 4.6 },
  "guard_statuses": [
    { "name": "MotionGuard", "layer": "L2", "decision": "PASS", "reason": "" }
  ],
  "timestamp": 1700000000.0
}
```

On connection, the last 50 events are replayed immediately.
A `{"type":"ping"}` keepalive is sent every 30 s.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8080` | Base URL for REST API calls |
| `NEXT_PUBLIC_WS_URL`  | `ws://localhost:8080`  | WebSocket base URL |

Create `dam-console/.env.local` to override:

```env
NEXT_PUBLIC_API_URL=http://192.168.1.100:8080
NEXT_PUBLIC_WS_URL=ws://192.168.1.100:8080
```

---

## Running tests

```bash
cd dam-console
npm test             # run all Jest tests
npm run test:watch   # watch mode
npm run test:ci      # CI mode with coverage
```
