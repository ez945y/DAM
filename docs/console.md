# DAM Console

The DAM Console is where operators and developers watch DAM make safety decisions in real time. Use it to answer four practical questions:

- Is the runtime connected and cycling?
- Did the latest action PASS, CLAMP, or REJECT?
- Which guard layer made the decision?
- Is latency still inside the cycle budget?

---

## Quick start

For normal project use, start the backend and console together:

```bash
make run
```

Then open:

- Console: `http://localhost:3000`
- API docs: `http://localhost:8080/docs`

For frontend development, run the API and console separately from the repo root and `dam-console/` folder.

---

## First Things To Check

| Question | Where to look | Healthy signal |
|----------|---------------|----------------|
| Is DAM connected? | Runtime status / connection badge | Connected, cycling, or ready state |
| Are actions safe? | Risk gauge and guard status | PASS or expected CLAMP events |
| Why was something rejected? | Event log and guard table | Guard layer, guard name, reason string |
| Is the loop too slow? | Cycle latency panel | Total latency below the configured budget |
| What should I inspect after an event? | MCAP Sessions | The cycle, guard result, and nearby context |

---

## Pages

### Dashboard `/`

Real-time overview of the running DAM runtime. This is the first page to open during a demo or safety investigation.

| Widget | Description |
|--------|-------------|
| **Risk Gauge** | SVG arc gauge showing current risk level (NORMAL → EMERGENCY) |
| **Stats cards** | Total cycles, reject count + rate, clamp count, average latency |
| **Runtime Control** | Start / Pause / Resume / Stop / E-STOP / Reset |
| **Cycle Latency** | Rolling area chart of the last 60 end-to-end cycle times |
| **Pipeline Breakdown** | Source / Policy / Guards / Sink split for the latest cycle, when available |
| **Guard Layers** | Per-layer L0-L3 guard latency bars, when available |
| **Deadline Margin** | Badge next to the latency panel header showing remaining headroom before the cycle budget deadline (green / amber / red) |
| **Guard Status** | Per-guard table: name, layer, last decision, reason |
| **Event Log** | Filterable scrolling event log with timestamps |

#### Reading Latency

The **Deadline Margin** badge shows how much time remains before the configured cycle budget is exceeded.

| Colour | Condition | Label |
|--------|-----------|-------|
| Green  | slack > 30 % of budget | `OK` |
| Amber  | 10–30 % of budget | `NEAR` |
| Red    | < 10 % of budget | `TIGHT` |
| Red    | Negative (over-budget) | `OVER` |

Colour mapping:

| Segment | Colour | Meaning |
|---------|--------|---------|
| Source | Indigo | `source.read()` — sensor acquisition |
| Policy | Amber | `policy.predict()` — model inference |
| Guards | Emerald | `validate()` — all guard checks combined |
| Sink | Blue | `sink.apply()` — action dispatch |
| L0 | Violet | OOD Detection guards |
| L1 | Green | Physical kinematics / motion guards |
| L2 | Emerald | Task execution guards |
| L3 | Light-green | Hardware Monitoring guards |

### Config `/config`

Visual Stackfile editor. Pick a template, configure adapters, edit joint limits, and export the generated YAML.

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

### MCAP Sessions `/mcap`

View and analyze recorded loopback sessions.

!!! info
    Requires `loopback.backend = "mcap"` in Stackfile and at least one violation to have triggered recording.

| Feature | Description |
|---------|-------------|
| **Session List** | All `.mcap` files in `output_dir`, sorted by date. Shows file size, violation count, clamp count, camera availability. |
| **Timeline View** | Horizontal timeline of 10,000 cycles (or current session). Each bar coloured by outcome: green=PASS, amber=CLAMP, red=REJECT/FAULT. Click to jump to cycle. |
| **Cycle Inspector** | When you click a cycle on the timeline, shows: joint angles, EE pose, force/torque, active guards, latency breakdown. |
| **Guard Result Detail** | Per-guard decision (PASS/CLAMP/REJECT/FAULT) with reason string and latency. Colour-coded by layer (L0–L3). |
| **Image Gallery** | If violation has images, displays side-by-side crops from violation ±2 seconds (pre/during/post context). Tap to enlarge. |
| **Latency Graphs** | Per-cycle latency for source / policy / guards / sink, plus per-layer (L0–L3) stacked area chart. |
| **Export** | Download filtered subset (date range, cycle range, specific guards) as CSV or new MCAP file for sharing. |

#### Quick workflow

1. In **Dashboard**, spot a high-risk cycle with a red badge
2. Click the cycle ID → jumps to **MCAP Sessions** with that cycle pre-selected
3. Inspect the guard that violated + surrounding context (±10 s)
4. Download the incident as a mini-MCAP for bug report

---

## WebSocket stream

The console subscribes to the live telemetry stream to provide real-time visual feedback. Most users do not need this section unless they are extending the console or debugging an integration.

Each control cycle, the console receives:
*   **State Updates**: JSON metadata including guard decisions, risk levels, and latency metrics.
*   **High-Speed Video**: Zero-latency binary image frames for all active cameras.

For low-level message formats and telemetry wiring, see [Services API → Telemetry](services-api.md#ws-wstelemetry).

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
    "layers":  { "L0": 2.1, "L2": 2.0, "L3": 1.3 },
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
