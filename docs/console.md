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
| **Cycle Latency** | Rolling area chart of the last 60 end-to-end cycle times |
| **Pipeline Breakdown** | Horizontal stacked bar showing Source / Policy / Guards / Sink split for the latest cycle (requires MetricBus) |
| **Guard Layers** | Per-layer (L0–L4) guard latency bars for the latest cycle (requires MetricBus) |
| **Deadline Margin** | Badge next to the latency panel header showing remaining headroom before the cycle budget deadline (green / amber / red) |
| **Guard Status** | Per-guard table: name, layer, last decision, reason |
| **Event Log** | Filterable scrolling event log with timestamps |

#### Deadline Margin badge

The **Deadline Margin** badge appears only when the backend `TelemetryService`
is wired with a `MetricBus` (i.e. `perf` data is present in the WebSocket
stream). It displays `slack_ms = deadline_ms − total_ms` with three visual states:

| Colour | Condition | Label |
|--------|-----------|-------|
| Green  | slack > 30 % of budget | `OK` |
| Amber  | 10–30 % of budget | `NEAR` |
| Red    | < 10 % of budget | `TIGHT` |
| Red    | Negative (over-budget) | `OVER` |

#### Pipeline Breakdown & Guard Layers

These two sub-charts appear inside the **Cycle Latency** panel only when the
`perf` field is present in the live WebSocket stream.  They use the same data
source as the `perf.stages` and `perf.layers` fields described in
[Services API → Telemetry](services-api.md#ws-wstelemetry).

Colour mapping:

| Segment | Colour | Meaning |
|---------|--------|---------|
| Source | Indigo | `source.read()` — sensor acquisition |
| Policy | Amber | `policy.predict()` — model inference |
| Guards | Emerald | `validate()` — all guard checks combined |
| Sink | Blue | `sink.apply()` — action dispatch |
| L0 | Violet | OOD Detection guards |
| L1 | Green | Preflight Simulation guards |
| L2 | Emerald | Motion Safety guards |
| L3 | Light-green | Task Execution guards |
| L4 | Red | Hardware Monitoring guards |

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
Each cycle the API pushes a JSON message.

The base fields are always present.  The optional `perf` object is included
when the backend `TelemetryService` is constructed with a `MetricBus`
reference — see [Services API → Telemetry](services-api.md#ws-wstelemetry)
for the full field reference and wiring instructions.

```json
{
  "type": "cycle",
  "cycle_id": 42,
  "trace_id": "3fa8...",
  "was_rejected": false,
  "was_clamped": false,
  "risk_level": "NORMAL",
  "fallback_triggered": null,
  "latency_ms": { "obs": 0.8, "policy": 2.1, "validate": 5.4, "sink": 0.4, "total": 8.7 },
  "guard_statuses": [
    { "name": "OODGuard",    "layer": "L0", "decision": "PASS", "reason": "" },
    { "name": "MotionGuard", "layer": "L2", "decision": "PASS", "reason": "" }
  ],
  "timestamp": 1700000000.0,

  "perf": {
    "stages":  { "source": 0.8, "policy": 2.1, "guards": 5.4, "sink": 0.4, "total": 8.7 },
    "layers":  { "L0": 2.1, "L2": 2.0, "L4": 1.3 },
    "guards":  { "OODGuard": 2.1, "MotionGuard": 1.0, "HardwareGuard": 1.3 },
    "deadline_ms": 20.0,
    "slack_ms": 11.3
  }
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
