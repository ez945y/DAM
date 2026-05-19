# Experiment Scripts

DAM ships the thesis evaluation runners (RQ1–RQ5) as native experiments that
write results to a configurable output directory and require only the standard
DAM Python environment plus `matplotlib` for plots.

All five are exposed through one registry (`dam.experiments`) and can be run
from three entry points:

- **Console** — the *Experiments* page (`run` / `artifacts` tabs); SVG/PNG
  preview inline, CSV via `GET /api/experiments/artifact`.
- **CLI** — `dam experiment list` and `dam experiment run <id> [flags]`.
- **HTTP** — `GET /api/experiments`, `POST /api/experiments/{id}/run`.

| RQ | Id | What it measures | Data source |
|----|----|------------------|-------------|
| RQ1 | `l0-calibration` | L0 threshold / FPR / FNR / EER | Parametric synthetic study |
| RQ2 | `boundary-scan` | L1/L2 interception curves | Real `guard.check()` runs |
| RQ3 | `usability` | False-trigger & success rate on benign legal-variation frames | Real L0–L2 guard runs |
| RQ4 | `latency-bench` | Guard runtime latency under 10/20/50 Hz budgets | Isolated Guard profiling |
| RQ5 | `failure-record-quality` | Completeness/classification/diversity of harvested failure records | Real violating-scenario runs |

RQ3 and RQ5 are real measurements driven by the live guard stack and the
shared production classifier — they are not hardcoded. RQ1 is an explicit
parametric synthetic calibration.

RQ4 is an isolated Guard profiling experiment. It measures the safety-monitoring
path from receiving an action proposal to outputting the validated action, and
excludes image preprocessing and policy inference time.

---

## Prerequisites

```bash
pip install matplotlib        # only needed for plot generation
# DAM itself must already be installed:
make setup                    # or: pip install -e .
```

---

## Experiment 1 — Boundary Precision Scan

**Script:** `scripts/run_boundary_scan.py`

**Purpose:** Quantifies how reliably L1 and L2 guards intercept actions as disturbance
intensity increases.  Four scenarios are swept, each varying one parameter that pushes
the robot toward a safety boundary.

### Scenarios

| ID | Guard | Parameter swept | Range |
|----|-------|-----------------|-------|
| L1-A | `MotionGuard` (L1) | Gaussian noise σ on joint positions | 0.05 – 0.50 rad |
| L1-B | `MotionGuard` (L1) | Velocity scale factor k | 1.2× – 3.0× |
| L2-A | `ExecutionGuard` (L2) | End-effector clearance d from boundary | +5 cm → −5 cm |
| L2-B | `ExecutionGuard` (L2) | Active node duration / T_timeout ratio | 0.5× – 2.0× |

Each disturbance level is tested for a fixed number of independent trials; the
interception rate (fraction of trials that produced CLAMP, REJECT, or FAULT) is
recorded per level.

### Usage

```bash
python scripts/run_boundary_scan.py [--trials N] [--outdir PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--trials` | `20` | Trials per (scenario, disturbance level) |
| `--outdir` | `data/exp1_boundary_scan/` | Directory for output files |

### Output

| File | Description |
|------|-------------|
| `results.csv` | One row per (scenario, level): `scenario`, `disturbance_label`, `disturbance_value`, `intercepted`, `trials`, `interception_rate` |
| `boundary_scan.png` | 4-panel figure — interception rate (%) vs disturbance value per scenario, with x50 and x90 reference lines |

A summary table is also printed to stdout at the end of the run.

### Interpreting the metrics

**x50** — The disturbance value at which the guard intercepts 50 % of actions.  This
marks where the guard starts to "feel" the boundary.

**x90** — The disturbance value at which the guard intercepts 90 % of actions.  This
marks where the guard is reliably enforcing the boundary.

**Steepness** — Defined as `x90 − x50` (in the same units as the disturbance axis).
A smaller value means the guard transitions sharply from permissive to restrictive,
indicating a tight, well-defined boundary.  A larger value indicates a gradual
transition that may be worth investigating.

### Example

```bash
# Quick validation with 50 trials
python scripts/run_boundary_scan.py --trials 50 --outdir results/boundary_scan

# High-fidelity run (slower)
python scripts/run_boundary_scan.py --trials 200 --outdir results/boundary_scan_hifi
```

---

## Experiment 4 — Guard Latency Benchmark

**Console/API id:** `latency-bench`

**Purpose:** Evaluates the RSMF runtime latency overhead at different control
frequencies and quantifies how gradually enabling Guard layers affects the
control-loop time budget.

The overall control system is split into policy inference and safety monitoring.
RQ4 profiles only the safety-monitoring module: the measured interval begins
when a Guard configuration receives an action proposal and ends when it produces
the validated action decision. The measurement excludes image preprocessing and
policy model inference so external module variance does not distort Guard-layer
latency.

The Console runs the benchmark in three sequential launches for 10 Hz, 20 Hz,
and 50 Hz. By default each launch is paced at the requested control frequency,
so 500 time steps take about 50 s at 10 Hz, 25 s at 20 Hz, and 10 s at 50 Hz.
Results are shown after each frequency finishes, so the table grows from
10 Hz to 20 Hz to 50 Hz instead of appearing only at the end.

The experiment evaluates four configurations:

| Configuration | Meaning |
|---------------|---------|
| `No Safety` | Baseline action-proposal loop without safety checks |
| `Rule-based Safety` | Deterministic motion, execution, and hardware checks |
| `OOD-only` | L0 perception anomaly detection only |
| `Full RSMF` | L0–L3 safety layers enabled |

### Usage

```bash
curl -X POST http://127.0.0.1:8080/api/experiments/latency-bench/run \
  -H 'Content-Type: application/json' \
  -d '{"params":{"fps_values":"10,20,50","steps_per_config":500}}'
```

| Flag | Default | Description |
|------|---------|-------------|
| `fps_values` | `10,20,50` | Control frequencies to evaluate; the Console runs these sequentially |
| `steps_per_config` | `500` | Time steps per safety configuration and frequency |
| `realtime` | `true` | Pace time steps at the requested FPS; set `false` only for quick smoke tests |
| `seed` | `42` | Deterministic observation/action proposal seed |
| `outdir` | `data/experiments/latency_bench/` | Directory for output files |

### Output

| File | Description |
|------|-------------|
| `results.csv` | One row per `(frequency, configuration)` with latency distribution and deadline miss rate |
| `latency_bench.png` | p95 Guard latency across 10/20/50 Hz with the budget reference line |
| `latency_bench.svg` | Inline SVG version of the p95 latency chart |

### Interpreting the results

The benchmark reports six statistics per frequency/configuration pair:

| Statistic | Meaning |
|-----------|---------|
| `mean_ms` | Average per-frame guard latency |
| `std_ms` | Standard deviation — indicates consistency |
| `p95_ms` | 95th-percentile latency — worst case for 1 in 20 frames |
| `p99_ms` | 99th-percentile latency — worst case for 1 in 100 frames |
| `max_ms` | Absolute worst observed frame |
| `deadline_miss_rate` | Fraction of time steps whose Guard latency exceeded the control-period budget |

The three control frequencies correspond to 100 ms, 50 ms, and 20 ms budgets.
Any single Guard processing time above the relevant budget is counted as a
deadline miss.

### Example

```bash
# Quick unpaced 20 Hz validation
python scripts/run_latency_bench.py --frames 200 --fps 20 --outdir results/latency_20hz

# Thesis-sized paced 50 Hz run
python scripts/run_latency_bench.py --frames 500 --fps 50 --realtime --outdir results/latency_50hz
```
