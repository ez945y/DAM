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
    python scripts/run_latency_bench.py [--frames N] [--fps FPS] [--realtime] [--outdir PATH]

    N       time steps per configuration — default 500
    FPS     control frequency budget     — default 50
    realtime pace time steps at FPS       — default off for CLI smoke tests
    outdir  output directory             — default ./data/exp3_latency/
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

# ── DAM imports ──────────────────────────────────────────────────────────────
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from scripts._bench_stackfiles import (
    JOINT_LOWER as _JOINT_LOWER,
)
from scripts._bench_stackfiles import (
    JOINT_UPPER as _JOINT_UPPER,
)
from scripts._bench_stackfiles import (
    MAX_VEL as _MAX_VEL,
)
from scripts._bench_stackfiles import (
    N_JOINTS as _N_JOINTS,
)
from scripts._bench_stackfiles import (
    build_runtime as _build_runtime,
)
from scripts._experiment_logging import configure_cli_logging

LOGGER = logging.getLogger(__name__)

# ── Synthetic data helpers ────────────────────────────────────────────────────


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


# ── Runtime construction ─────────────────────────────────────────────────────
# Each benchmark config drives a `GuardRuntime` built from a real stackfile —
# the same path production uses.  Boundary params come from the stackfile;
# nothing is hand-rolled in this script.


def _build_runtime_for(guard_names: list[str]):
    return _build_runtime(
        ood="ood" in guard_names,
        motion="motion" in guard_names,
        execution="execution" in guard_names,
        hardware="hardware" in guard_names,
    )[0]


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
    runtime = _build_runtime_for(guard_names) if guard_names else None
    latencies_ms: list[float] = []

    for i in range(frames):
        obs = _make_nominal_obs(rng)
        action = _make_nominal_action(obs)
        now = time.monotonic()
        latencies_ms.append(_measure_runtime_ms(runtime, obs, action, i, now))

    return _latency_stats(label, frames, latencies_ms, budget_ms)


def run_frequency(
    frames: int,
    rng: np.random.Generator,
    budget_ms: float,
    *,
    realtime: bool = False,
    pace_seconds: float = 0.0,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    runtime_sets = [
        (label, _build_runtime_for(guard_names) if guard_names else None)
        for label, guard_names in _CONFIGS
    ]
    latencies: dict[str, list[float]] = {label: [] for label, _ in runtime_sets}
    visual_step_ms = (pace_seconds * 1000.0 / frames) if pace_seconds > 0 else 0.0
    progress_every = max(1, frames // 10)

    for idx in range(frames):
        cycle_t0 = time.perf_counter()
        obs = _make_nominal_obs(rng)
        action = _make_nominal_action(obs)
        now = time.monotonic()
        for label, runtime in runtime_sets:
            latencies[label].append(_measure_runtime_ms(runtime, obs, action, idx, now))
        if realtime:
            elapsed_ms = (time.perf_counter() - cycle_t0) * 1000.0
            sleep_ms = budget_ms - elapsed_ms
        elif visual_step_ms > 0:
            elapsed_ms = (time.perf_counter() - cycle_t0) * 1000.0
            sleep_ms = visual_step_ms - elapsed_ms
        else:
            sleep_ms = 0.0
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
        if progress and ((idx + 1) % progress_every == 0 or idx + 1 == frames):
            progress(idx + 1, frames)

    return [_latency_stats(label, frames, latencies[label], budget_ms) for label, _ in runtime_sets]


def _measure_runtime_ms(
    runtime: Any,
    obs: Observation,
    action: ActionProposal,
    cycle_id: int,
    now: float,
) -> float:
    """Time one ``runtime.validate(...)`` call — the production hot path."""
    if runtime is None:
        # "No Safety" config: still pay observation+action construction overhead
        # (already done by caller) but skip guard work entirely.
        return 0.0
    t0 = time.perf_counter()
    runtime.validate(obs, action, f"bench-{cycle_id}", now=now)
    return (time.perf_counter() - t0) * 1000.0


def _latency_stats(
    label: str,
    frames: int,
    latencies_ms: list[float],
    budget_ms: float | None = None,
) -> dict:
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
    LOGGER.info("CSV saved: %s", path)


def plot_results(results: list[dict], outdir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        LOGGER.warning("matplotlib not installed; skipping plot generation.")
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
        latency_max = max(float(r.get("max_ms", r["p95_ms"])) for r in results)
        ax.set_ylim(0, max(0.01, latency_max * 1.18))
        budget_text = "Budgets: " + " / ".join(
            f"{fps:g} Hz={1000.0 / fps:g} ms" for fps in fps_values
        )
        ax.text(
            0.99,
            0.97,
            budget_text,
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            color="#666",
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
    LOGGER.info("Plot saved: %s", out)
    plt.close(fig)


def print_table(results: list[dict]) -> None:
    LOGGER.info("Latency results")
    for r in results:
        LOGGER.info(
            "fps=%.0f config=%s mean_ms=%.3f std_ms=%.3f p95_ms=%.3f "
            "p99_ms=%.3f max_ms=%.3f miss_pct=%.2f",
            float(r.get("target_fps", 0)),
            r["config"],
            r["mean_ms"],
            r["std_ms"],
            r["p95_ms"],
            r["p99_ms"],
            r["max_ms"],
            100 * r.get("deadline_miss_rate", 0.0),
        )


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    configure_cli_logging()
    parser = argparse.ArgumentParser(description="DAM Experiment 3 — Latency Benchmark")
    parser.add_argument("--frames", type=int, default=500, help="Frames per configuration")
    parser.add_argument("--fps", type=float, default=50.0, help="Control frequency budget")
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Pace time steps at the requested FPS instead of running as fast as possible",
    )
    parser.add_argument(
        "--pace-seconds",
        type=float,
        default=0.0,
        help="Visual pacing duration for non-realtime runs",
    )
    parser.add_argument("--outdir", type=str, default="data/exp3_latency")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(42)
    results: list[dict] = []

    budget_ms = 1000.0 / args.fps
    if args.realtime:
        LOGGER.info("Running all Guard configs for %d time steps at %g Hz", args.frames, args.fps)
        suite = run_frequency(args.frames, rng, budget_ms, realtime=True)
    elif args.pace_seconds > 0:
        LOGGER.info(
            "Running all Guard configs for %d time steps over %gs visual pacing",
            args.frames,
            args.pace_seconds,
        )
        suite = run_frequency(args.frames, rng, budget_ms, pace_seconds=args.pace_seconds)
    else:
        suite = []
        for label, guard_names in _CONFIGS:
            LOGGER.info("Running %s (%d frames)", label, args.frames)
            suite.append(run_config(label, guard_names, args.frames, rng, budget_ms=budget_ms))

    for r in suite:
        r["target_fps"] = args.fps
        r["budget_ms"] = budget_ms
        results.append(r)
        LOGGER.info(
            "%s mean_ms=%.3f p95_ms=%.3f p99_ms=%.3f max_ms=%.3f",
            r["config"],
            r["mean_ms"],
            r["p95_ms"],
            r["p99_ms"],
            r["max_ms"],
        )

    write_csv(results, outdir / "results.csv")
    plot_results(results, outdir)
    print_table(results)

    full = results[-1]
    LOGGER.info("Full RSMF deadline miss rate: %.2f%%", 100 * full["deadline_miss_rate"])


if __name__ == "__main__":
    main()
