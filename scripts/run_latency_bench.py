"""RQ4 isolated Guard latency profiling helper.

This script profiles only the Guard module path: receiving an action proposal,
running the selected safety checks, and producing the validated action decision.
It intentionally excludes image preprocessing and policy inference so external
module jitter does not contaminate Guard-layer latency.

The default suite evaluates four safety configurations:

  No Safety           baseline loop/action proposal overhead
  Rule-based Safety   deterministic motion, execution, and hardware checks
  OOD-only            L0 perception anomaly detector only
  Full RSMF           L0 through L3 safety layers

Each configuration runs for a fixed number of time steps and reports mean,
standard deviation, p95, p99, max latency, and deadline miss rate for the chosen
control frequency budget.

Usage
-----
    python scripts/run_latency_bench.py [--frames N] [--fps FPS] [--outdir PATH]

    N       time steps per configuration — default 500
    FPS     control frequency budget     — default 50
    outdir  output directory             — default ./data/exp3_latency/
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    ("No Safety", []),
    ("Rule-based Safety", ["motion", "execution", "hardware"]),
    ("OOD-only", ["ood"]),
    ("Full RSMF", ["ood", "motion", "execution", "hardware"]),
]


def run_config(
    label: str,
    guard_names: list[str],
    frames: int,
    rng: np.random.Generator,
    budget_ms: float | None = None,
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
        "deadline_miss_rate": float(np.mean(arr > budget_ms)) if budget_ms else 0.0,
        "_raw": arr,  # kept for plotting; not written to CSV
    }


# ── Reporting ─────────────────────────────────────────────────────────────────


def write_csv(results: list[dict], path: Path) -> None:
    keys = [
        "target_fps",
        "budget_ms",
        "config",
        "frames",
        "mean_ms",
        "std_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
        "deadline_miss_rate",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[k for k in keys if k in results[0]])
        writer.writeheader()
        for r in results:
            writer.writerow({k: r[k] for k in keys if k in r})
    print(f"CSV saved: {path}")


def plot_results(results: list[dict], outdir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return

    fig, ax = plt.subplots(figsize=(9, 4.8))
    if all("target_fps" in r for r in results):
        configs = list(dict.fromkeys(str(r["config"]) for r in results))
        for config in configs:
            config_rows = sorted(
                (r for r in results if str(r["config"]) == config),
                key=lambda row: float(row["target_fps"]),
            )
            ax.plot(
                [float(r["target_fps"]) for r in config_rows],
                [float(r["p95_ms"]) for r in config_rows],
                marker="o",
                linewidth=2,
                label=config,
            )
        fps_values = sorted({float(r["target_fps"]) for r in results})
        ax.plot(
            fps_values, [1000.0 / fps for fps in fps_values], "k--", linewidth=1.5, label="Budget"
        )
        ax.set_xlabel("Control frequency (Hz)")
        ax.set_title("RQ4 — Guard p95 Latency by Control Frequency")
    else:
        labels = [r["config"] for r in results]
        means = [r["mean_ms"] for r in results]
        stds = [r["std_ms"] for r in results]
        xs = np.arange(len(labels))
        ax.errorbar(xs, means, yerr=stds, fmt="o-", linewidth=2, capsize=4, label="Mean ± Std")
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_title("RQ4 — Guard Latency")
    ax.set_ylabel("Guard latency (ms)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out = outdir / "latency_bench.png"
    fig.savefig(out, dpi=150)
    print(f"Plot saved: {out}")
    plt.close(fig)


def print_table(results: list[dict]) -> None:
    print(
        f"\n{'FPS':>5} {'Config':<20} {'Mean':>8} {'Std':>7} "
        f"{'p95':>7} {'p99':>7} {'Max':>7} {'Miss %':>8}"
    )
    print("-" * 84)
    for r in results:
        print(
            f"{float(r.get('target_fps', 0)):>5.0f} "
            f"{r['config']:<20} "
            f"{r['mean_ms']:>7.3f} "
            f"{r['std_ms']:>7.3f} "
            f"{r['p95_ms']:>7.3f} "
            f"{r['p99_ms']:>7.3f} "
            f"{r['max_ms']:>7.3f} "
            f"{100 * r.get('deadline_miss_rate', 0.0):>7.2f}%"
        )
    print()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM Experiment 3 — Latency Benchmark")
    parser.add_argument("--frames", type=int, default=500, help="Frames per configuration")
    parser.add_argument("--fps", type=float, default=50.0, help="Control frequency budget")
    parser.add_argument("--outdir", type=str, default="data/exp3_latency")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(42)
    results: list[dict] = []

    budget_ms = 1000.0 / args.fps
    for label, guard_names in _CONFIGS:
        print(f"Running {label} ({args.frames} frames)…")
        r = run_config(label, guard_names, args.frames, rng, budget_ms=budget_ms)
        r["target_fps"] = args.fps
        r["budget_ms"] = budget_ms
        results.append(r)
        print(
            f"  mean={r['mean_ms']:.3f}ms  p95={r['p95_ms']:.3f}ms  "
            f"p99={r['p99_ms']:.3f}ms  max={r['max_ms']:.3f}ms"
        )

    write_csv(results, outdir / "results.csv")
    plot_results(results, outdir)
    print_table(results)

    full = results[-1]
    print(f"Full RSMF deadline miss rate: {100 * full['deadline_miss_rate']:.2f}%")


if __name__ == "__main__":
    main()
