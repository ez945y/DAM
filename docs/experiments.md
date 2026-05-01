# Experiment Scripts

DAM ships two experiment scripts for validating guard behaviour and measuring runtime
overhead.  Both write results to a configurable output directory and require only the
standard DAM Python environment plus `matplotlib` for plots.

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

## Experiment 3 — Guard Latency Benchmark

**Script:** `scripts/run_latency_bench.py`

**Purpose:** Measures the per-frame wall-clock latency added by the guard stack across
four cumulative configurations, verifying that the full stack stays within the 15 ms
per-frame budget.

### Configurations

| Config | Guards active |
|--------|---------------|
| L0-only | `OODGuard` |
| L0 + L1 | + `MotionGuard` |
| L0 + L1 + L2 | + `ExecutionGuard` |
| Full (L0–L3) | + `HardwareGuard` |

Each configuration is run on `--frames` frames of synthetic nominal data drawn from a
fixed random seed.  The OOD guard uses the Welford online estimator (no model file
required).

### Usage

```bash
python scripts/run_latency_bench.py [--frames N] [--outdir PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--frames` | `500` | Frames per configuration |
| `--outdir` | `data/exp3_latency/` | Directory for output files |

### Output

| File | Description |
|------|-------------|
| `results.csv` | One row per configuration: `config`, `frames`, `mean_ms`, `std_ms`, `p95_ms`, `p99_ms`, `max_ms` |
| `latency_bench.png` | Mean ± std latency per configuration with the 15 ms target line |

A formatted summary table is printed to stdout, and the final line reports whether the
full-stack mean latency meets the target (`PASS ✓` / `FAIL ✗`).

### Interpreting the results

The benchmark reports five statistics per configuration:

| Statistic | Meaning |
|-----------|---------|
| `mean_ms` | Average per-frame guard latency |
| `std_ms` | Standard deviation — indicates consistency |
| `p95_ms` | 95th-percentile latency — worst case for 1 in 20 frames |
| `p99_ms` | 99th-percentile latency — worst case for 1 in 100 frames |
| `max_ms` | Absolute worst observed frame |

The 15 ms target applies to the mean of the full (L0–L3) configuration.  If `mean_ms`
exceeds 15 ms, investigate which guard contributes the largest incremental cost by
comparing the cumulative configs in the CSV.

### Example

```bash
# Quick run
python scripts/run_latency_bench.py --frames 200 --outdir results/latency

# More statistically stable
python scripts/run_latency_bench.py --frames 2000 --outdir results/latency_stable
```
