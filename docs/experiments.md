# Experiment Scripts

DAM ships the thesis evaluation runners (RQ1–RQ5) as native experiments that
write results to a configurable output directory and require only the standard
DAM Python environment plus `matplotlib` for plots.

All five are exposed through one registry (`dam.experiments`) and can be run
from three entry points:

- **Console** — the *Experiments* page (`run` / `artifacts` tabs); PNG
  previews and result statistics are shown inline.
- **CLI** — `dam experiment list` and `dam experiment run <id> [flags]`.
- **HTTP** — `GET /api/experiments`, `POST /api/experiments/{id}/run`.

| RQ | Id | What it measures | Data source |
|----|----|------------------|-------------|
| RQ1 | `l0-calibration` | L0 Real-NVP per-frame NLL separation | HF datasets: normal / legal variation / abnormal-A |
| RQ2 | `boundary-scan` | L1/L2 interception curves | Real `guard.check()` runs |
| RQ3 | `usability` | False-trigger & success rate on benign legal-variation frames | Real L0–L2 guard runs |
| RQ4 | `latency-bench` | Guard runtime latency under 10/20/50 Hz budgets | Isolated Guard profiling |
| RQ5 | `failure-record-quality` | Completeness/classification/diversity of harvested failure records | Real violating-scenario runs |

RQ3 and RQ5 drive the live guard stack and shared production classifier. RQ1 is
an offline L0 evaluation harness: it uses DAM's `OODContext` feature path and
public OOD backends to train Real-NVP on normal observations, then compares
per-frame NLL across the normal test set, legal-variation test set, and
abnormal-A test set. An optional RQ1 flag also scores the same features with
Welford z-score and MemoryBank nearest-neighbor distance.

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

### RQ1 Options

```bash
python scripts/run_l0_calibration.py
python scripts/run_l0_calibration.py --compare-ood-methods
python scripts/run_l0_calibration.py --vision-model mobilenet_v3_large
dam experiment run l0-calibration --compare-ood-methods
```

Default RQ1 output is the Real-NVP per-frame NLL comparison. With
`--compare-ood-methods`, `results.csv` additionally includes Welford and
MemoryBank rows using the shared columns `method`, `score_name`, and
`score_value`; Real-NVP rows also fill the `nll` column.

RQ1 previews are generated as PNG (`l0_calibration.png`). The old SVG median
bar preview is intentionally not generated because negative Real-NVP NLL values
make the simple SVG bar chart misleading. RQ1 also uses a local cache for
HuggingFace observations and the trained Real-NVP flow under
`data/experiments/l0_calibration/cache`; pass `--no-cache` to force a full
reload/retrain.

Default RQ1 features are derived from `observation.state`; the dataset
`action` column is not currently model input and is reported as such in the
console summary. When `--vision-model` is set, RQ1 loads video frames and
fuses pretrained image embeddings with state features. With subsampling, only
frames that actually carry an image are scored, and the console reports the
attached/available frame count.

`nll_sigma` controls the Real-NVP decision threshold:
`threshold = training_NLL_mean + nll_sigma * training_NLL_std`. Larger values
are more tolerant and usually reduce false positives; smaller values are more
sensitive and usually detect abnormal frames earlier.

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
and 50 Hz. By default each launch uses a short visual pacing window so the page
does not block for more than a minute, while still evaluating deadline miss
against the 100/50/20 ms control budgets. Set `realtime=true` for a wall-clock
paced run. Results are shown after each frequency finishes, so the table grows
from 10 Hz to 20 Hz to 50 Hz instead of appearing only at the end.

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
| `realtime` | `false` | Sleep the full control period when `true` |
| `pace_seconds_per_fps` | `4` | Visual pacing duration for each FPS when `realtime=false` |
| `seed` | `42` | Deterministic observation/action proposal seed |
| `outdir` | `data/experiments/latency_bench/` | Directory for output files |

### Output

| File | Description |
|------|-------------|
| `results.csv` | One row per `(frequency, configuration)` with latency distribution and deadline miss rate |
| `latency_bench.png` | p95 Guard latency across 10/20/50 Hz with compact budget labels |

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
