"""Experiment 3 — Latency Overhead Benchmark (實驗三：延遲開銷評估).

Measures per-frame guard processing latency across four cumulative configurations:

  ① L0-only           (OOD inference baseline)
  ② L0 + L1           (+ Physical Kinematics)
  ③ L0 + L1 + L2      (+ Task Execution)
  ④ Full (L0+L1+L2+L3) (+ Hardware Monitoring)

Each configuration runs for a fixed number of frames on synthetic data that
mimics a Pick & Place observation.  Reports mean ± std, p95, p99, and max
latency per configuration.  Outputs a CSV and a matplotlib figure with a
15 ms target line.

Usage
-----
    python scripts/run_latency_bench.py [--frames N] [--outdir PATH]

    N       frames per configuration — default 500
    outdir  output directory         — default ./data/exp3_latency/
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import numpy as np

from dam.guard.builtin.execution import ExecutionGuard
from dam.guard.builtin.hardware import HardwareGuard
from dam.guard.builtin.motion import MotionGuard

# ── DAM imports ──────────────────────────────────────────────────────────────
from dam.guard.builtin.ood import OODGuard
from dam.types.action import ActionProposal
from dam.types.observation import Observation

# ── Synthetic data helpers ────────────────────────────────────────────────────

_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_MAX_VEL = np.full(_N_JOINTS, 1.5)
_WORKSPACE_BOUNDS = [[-0.40, 0.40], [-0.40, 0.40], [0.02, 0.60]]


def _make_nominal_obs(rng: np.random.Generator) -> Observation:
    """Randomise joint positions within ±50 % of limits to get realistic variance."""
    pos = rng.uniform(_JOINT_LOWER * 0.5, _JOINT_UPPER * 0.5)
    vel = rng.uniform(-_MAX_VEL * 0.3, _MAX_VEL * 0.3)
    ee = np.array([0.0, 0.0, 0.30, 0, 0, 0, 1], dtype=np.float64)
    return Observation(
        timestamp=time.monotonic(),
        joint_positions=pos,
        joint_velocities=vel,
        end_effector_pose=ee,
    )


def _make_nominal_action(obs: Observation) -> ActionProposal:
    return ActionProposal(
        target_joint_positions=obs.joint_positions,
        target_joint_velocities=obs.joint_velocities,
    )


# ── Guard instances ───────────────────────────────────────────────────────────


def _make_ood() -> OODGuard:
    import dam

    cls = dam.guard("L0")(OODGuard)
    g = cls(backend="welford")  # no model needed; Welford warms up online
    g._guard_name = "ood"
    return g


def _make_motion() -> MotionGuard:
    import dam

    cls = dam.guard("L1")(MotionGuard)
    g = cls()
    g._guard_name = "motion"
    return g


_MOTION_KWARGS = dict(
    upper=_JOINT_UPPER.tolist(),
    lower=_JOINT_LOWER.tolist(),
    max_velocity=_MAX_VEL.tolist(),
    bounds=_WORKSPACE_BOUNDS,
)


def _make_execution() -> ExecutionGuard:
    import dam

    cls = dam.guard("L2")(ExecutionGuard)
    g = cls()
    g._guard_name = "execution"
    return g


def _make_hardware() -> HardwareGuard:
    # HardwareGuard already has @dam.guard(layer="L3") — no extra decoration needed.
    g = HardwareGuard()
    g._guard_name = "hardware"
    return g


# ── Benchmark runner ──────────────────────────────────────────────────────────

_CONFIGS = [
    ("① L0-only", ["ood"]),
    ("② L0+L1", ["ood", "motion"]),
    ("③ L0+L1+L2", ["ood", "motion", "execution"]),
    ("④ Full L0-L3", ["ood", "motion", "execution", "hardware"]),
]


def run_config(
    label: str,
    guard_names: list[str],
    frames: int,
    rng: np.random.Generator,
) -> dict:
    # Build only the requested guard instances.
    guards: dict[str, object] = {}
    if "ood" in guard_names:
        guards["ood"] = _make_ood()
    if "motion" in guard_names:
        guards["motion"] = _make_motion()
    if "execution" in guard_names:
        guards["execution"] = _make_execution()
    if "hardware" in guard_names:
        guards["hardware"] = _make_hardware()

    latencies_ms: list[float] = []

    for _ in range(frames):
        obs = _make_nominal_obs(rng)
        action = _make_nominal_action(obs)
        now = time.monotonic()

        t0 = time.perf_counter()

        if "ood" in guards:
            guards["ood"].check(obs=obs)  # type: ignore[union-attr]

        if "motion" in guards:
            guards["motion"].check(obs=obs, action=action, **_MOTION_KWARGS)  # type: ignore[union-attr]

        if "execution" in guards:
            guards["execution"].check(obs=obs, active_containers=[], node_start_times={})  # type: ignore[union-attr]

        if "hardware" in guards:
            guards["hardware"].check(obs=obs, now=now)  # type: ignore[union-attr]

        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

    arr = np.array(latencies_ms)
    return {
        "config": label,
        "frames": frames,
        "mean_ms": float(np.mean(arr)),
        "std_ms": float(np.std(arr)),
        "p95_ms": float(np.percentile(arr, 95)),
        "p99_ms": float(np.percentile(arr, 99)),
        "max_ms": float(np.max(arr)),
        "_raw": arr,  # kept for plotting; not written to CSV
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

_TARGET_MS = 15.0


def write_csv(results: list[dict], path: Path) -> None:
    keys = ["config", "frames", "mean_ms", "std_ms", "p95_ms", "p99_ms", "max_ms"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in keys})
    print(f"CSV saved: {path}")


def plot_results(results: list[dict], outdir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return

    labels = [r["config"] for r in results]
    means = [r["mean_ms"] for r in results]
    stds = [r["std_ms"] for r in results]

    fig, ax = plt.subplots(figsize=(8, 4))
    xs = np.arange(len(labels))
    ax.errorbar(xs, means, yerr=stds, fmt="o-", linewidth=2, capsize=4, label="Mean ± Std")
    ax.axhline(
        _TARGET_MS, color="red", linestyle="--", linewidth=1.5, label=f"{_TARGET_MS} ms target"
    )
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Guard latency (ms)")
    ax.set_title("Experiment 3 — Cumulative Guard Latency")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out = outdir / "latency_bench.png"
    fig.savefig(out, dpi=150)
    print(f"Plot saved: {out}")
    plt.close(fig)


def print_table(results: list[dict]) -> None:
    print(f"\n{'Config':<22} {'Mean':>8} {'Std':>7} {'p95':>7} {'p99':>7} {'Max':>7}  Target")
    print("-" * 70)
    for r in results:
        meets = "✓" if r["mean_ms"] < _TARGET_MS else "✗"
        print(
            f"  {r['config']:<20} "
            f"{r['mean_ms']:>7.3f} "
            f"{r['std_ms']:>7.3f} "
            f"{r['p95_ms']:>7.3f} "
            f"{r['p99_ms']:>7.3f} "
            f"{r['max_ms']:>7.3f}  {meets}"
        )
    print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM Experiment 3 — Latency Benchmark")
    parser.add_argument("--frames", type=int, default=500, help="Frames per configuration")
    parser.add_argument("--outdir", type=str, default="data/exp3_latency")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(42)
    results: list[dict] = []

    for label, guard_names in _CONFIGS:
        print(f"Running {label} ({args.frames} frames)…")
        r = run_config(label, guard_names, args.frames, rng)
        results.append(r)
        print(
            f"  mean={r['mean_ms']:.3f}ms  p95={r['p95_ms']:.3f}ms  "
            f"p99={r['p99_ms']:.3f}ms  max={r['max_ms']:.3f}ms"
        )

    write_csv(results, outdir / "results.csv")
    plot_results(results, outdir)
    print_table(results)

    full = results[-1]
    status = "PASS ✓" if full["mean_ms"] < _TARGET_MS else "FAIL ✗"
    print(
        f"Full config mean latency: {full['mean_ms']:.3f} ms  "
        f"(target < {_TARGET_MS} ms)  → {status}"
    )


if __name__ == "__main__":
    main()
