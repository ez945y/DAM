"""Experiment — L0 OOD Calibration (RQ1).

Trains a real OODGuard (memory_bank backend) on normal robot observations,
then evaluates on three populations — normal, legal-variation, and OOD —
sweeping ``nn_threshold`` to produce verifiable FPR/FNR/EER curves.

All numbers come from real ``OODGuard.check()`` decisions running through
the production callback pipeline. Nothing is hardcoded.

Populations
-----------
normal:
    Drawn from the same tight distribution used for training.
    Should PASS at the working threshold → FPR = fraction that REJECT.

legal_variation:
    Slight perturbations within deployment tolerance (object shift,
    perception jitter).  Should also PASS → legal_fpr counts how many
    of these an operator would see flagged.

ood:
    Clearly different joint configurations — large offsets, extreme values,
    novel postures the guard has never seen.
    Should REJECT → FNR = fraction that PASS.

Usage
-----
    python scripts/run_l0_calibration.py [--train-samples N] [--eval-samples N]
                                          [--seed S] [--outdir PATH]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from dam.guard.builtin.ood import OODGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision

_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_JOINT_MID = (_JOINT_UPPER + _JOINT_LOWER) / 2
_JOINT_RANGE = _JOINT_UPPER - _JOINT_LOWER


def _training_obs(rng: np.random.Generator) -> Observation:
    """Covers the full legal operating envelope — nominal + deployment variations."""
    sigma = rng.choice([0.05, 0.08, 0.12])
    pos = _JOINT_MID + rng.normal(0.0, sigma, _N_JOINTS) * _JOINT_RANGE
    pos = np.clip(pos, _JOINT_LOWER, _JOINT_UPPER)
    return Observation(timestamp=time.monotonic(), joint_positions=pos)


def _normal_obs(rng: np.random.Generator) -> Observation:
    pos = _JOINT_MID + rng.normal(0.0, 0.05, _N_JOINTS) * _JOINT_RANGE
    pos = np.clip(pos, _JOINT_LOWER, _JOINT_UPPER)
    return Observation(timestamp=time.monotonic(), joint_positions=pos)


def _legal_variation_obs(rng: np.random.Generator) -> Observation:
    pos = _JOINT_MID + rng.normal(0.0, 0.12, _N_JOINTS) * _JOINT_RANGE
    pos = np.clip(pos, _JOINT_LOWER, _JOINT_UPPER)
    return Observation(timestamp=time.monotonic(), joint_positions=pos)


OOD_SCENARIOS = [
    "sensor_fault",
    "joint_jam",
    "external_perturbation",
    "corrupted_state",
    "partial_failure",
]


def _ood_obs_tagged(scenario: str, rng: np.random.Generator) -> Observation:
    if scenario == "sensor_fault":
        pos = np.zeros(_N_JOINTS)
    elif scenario == "joint_jam":
        pos = np.where(rng.random(_N_JOINTS) > 0.5, _JOINT_UPPER, _JOINT_LOWER)
    elif scenario == "external_perturbation":
        pos = _JOINT_MID + rng.choice([-1, 1], _N_JOINTS) * _JOINT_RANGE * rng.uniform(
            0.4, 0.8, _N_JOINTS
        )
    elif scenario == "corrupted_state":
        pos = rng.uniform(-5.0, 5.0, _N_JOINTS)
    else:
        pos = _JOINT_MID.copy()
        bad_joint = rng.integers(0, _N_JOINTS)
        pos[bad_joint] = _JOINT_UPPER[bad_joint] * rng.uniform(0.9, 1.5)
    return Observation(timestamp=time.monotonic(), joint_positions=pos)


def _ood_obs(rng: np.random.Generator) -> Observation:
    return _ood_obs_tagged(rng.choice(OOD_SCENARIOS), rng)


def _is_reject(result: object) -> bool:
    return getattr(result, "decision", None) in (
        GuardDecision.REJECT,
        GuardDecision.FAULT,
    )


def _eval_at_threshold(
    guard: OODGuard,
    population: list[Observation],
    threshold: float,
) -> float:
    rejects = 0
    for obs in population:
        result = guard.check(obs=obs, nn_threshold=threshold)
        if _is_reject(result):
            rejects += 1
    return rejects / len(population) if population else 0.0


def run_calibration(
    train_samples: int = 200,
    eval_samples: int = 120,
    n_thresholds: int = 40,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)

    train_obs = [_training_obs(rng) for _ in range(train_samples)]
    normal_eval = [_normal_obs(rng) for _ in range(eval_samples)]
    legal_eval = [_legal_variation_obs(rng) for _ in range(eval_samples)]
    ood_eval = [_ood_obs(rng) for _ in range(eval_samples)]

    guard = OODGuard(backend="memory_bank")
    precompute_injection(guard, {})
    guard.train(train_obs)

    diag = guard.diagnostics()
    print(f"  Trained: bank_size={diag['bank_size']}, backend={diag['bank_backend']}")

    z_normals = [guard._extractor.extract(o) for o in normal_eval]
    z_legals = [guard._extractor.extract(o) for o in legal_eval]
    z_oods = [guard._extractor.extract(o) for o in ood_eval]
    normal_dists = [guard._bank.nearest_distance(z) for z in z_normals]
    legal_dists = [guard._bank.nearest_distance(z) for z in z_legals]
    ood_dists = [guard._bank.nearest_distance(z) for z in z_oods]
    dist_lo = max(0.0, min(min(normal_dists), min(ood_dists)) * 0.5)
    dist_hi = max(max(normal_dists), max(ood_dists)) * 1.2
    print(
        f"  Distance distributions (p50/p95):\n"
        f"    normal:  p50={np.median(normal_dists):.3f}  p95={np.percentile(normal_dists, 95):.3f}\n"
        f"    legal:   p50={np.median(legal_dists):.3f}  p95={np.percentile(legal_dists, 95):.3f}\n"
        f"    ood:     p50={np.median(ood_dists):.3f}  p5={np.percentile(ood_dists, 5):.3f}"
    )
    separation = float(np.percentile(ood_dists, 5)) - float(np.percentile(legal_dists, 95))
    print(
        f"  Separation gap (OOD p5 - legal p95): {separation:.3f}"
        f" {'✓ clear' if separation > 0 else '⚠ overlap'}"
    )

    thresholds = np.linspace(dist_lo, dist_hi, n_thresholds)

    rows: list[dict] = []
    best_eer = {"threshold": 0.0, "eer_gap": 1.0, "fpr": 0.0, "fnr": 0.0}

    for tau in thresholds:
        tau_f = float(tau)
        fpr = _eval_at_threshold(guard, normal_eval, tau_f)
        fnr = 1.0 - _eval_at_threshold(guard, ood_eval, tau_f)
        legal_fpr = _eval_at_threshold(guard, legal_eval, tau_f)
        gap = abs(fpr - fnr)
        if gap < best_eer["eer_gap"]:
            best_eer = {"threshold": tau_f, "eer_gap": gap, "fpr": fpr, "fnr": fnr}
        rows.append(
            {
                "threshold": round(tau_f, 4),
                "fpr": round(fpr, 4),
                "fnr": round(fnr, 4),
                "legal_variation_fpr": round(legal_fpr, 4),
            }
        )
        print(f"  τ={tau_f:.4f}  FPR={fpr:.3f}  FNR={fnr:.3f}  legal_FPR={legal_fpr:.3f}")

    eer = (best_eer["fpr"] + best_eer["fnr"]) / 2.0
    legal_fpr_at_eer = min(rows, key=lambda r: abs(r["threshold"] - best_eer["threshold"]))[
        "legal_variation_fpr"
    ]

    # Operational threshold: best detection rate where FPR<5% AND legal_FPR<10%
    op_candidates = [r for r in rows if r["fpr"] <= 0.05 and r["legal_variation_fpr"] <= 0.10]
    if op_candidates:
        op_best = min(op_candidates, key=lambda r: r["fnr"])
        op_threshold = op_best["threshold"]
        op_detection_rate = round(1.0 - op_best["fnr"], 4)
        op_fpr = op_best["fpr"]
        op_legal_fpr = op_best["legal_variation_fpr"]
    else:
        op_threshold = None
        op_detection_rate = 0.0
        op_fpr = None
        op_legal_fpr = None

    deployable = op_threshold is not None and op_detection_rate >= 0.80

    summary = {
        "eer_threshold": round(best_eer["threshold"], 4),
        "eer": round(eer, 4),
        "fpr_at_eer": round(best_eer["fpr"], 4),
        "fnr_at_eer": round(best_eer["fnr"], 4),
        "legal_variation_fpr_at_eer": round(legal_fpr_at_eer, 4),
        "operational_threshold": op_threshold,
        "operational_detection_rate": op_detection_rate,
        "operational_fpr": op_fpr,
        "operational_legal_fpr": op_legal_fpr,
        "deployable": deployable,
        "train_samples": train_samples,
        "eval_samples_per_population": eval_samples,
        "bank_size": diag["bank_size"],
        "bank_backend": diag["bank_backend"],
    }

    print(f"\n  EER = {eer:.4f} at threshold = {best_eer['threshold']:.4f}")
    print(f"  Legal-variation FPR at EER = {legal_fpr_at_eer:.4f}")
    if op_threshold is not None:
        print(
            f"  Operational: threshold={op_threshold:.4f}  "
            f"detection={op_detection_rate:.1%}  FPR={op_fpr}  legal_FPR={op_legal_fpr}"
        )
    else:
        print("  Operational: NO threshold satisfies FPR<5% AND legal_FPR<10%")
    # Temporal smoothing effect: if single-frame FPR=p, then N-consecutive FPR≈p^N
    smoothing_frames = [1, 2, 3, 5]
    if op_fpr is not None:
        smoothed_fpr = {n: round(op_fpr**n, 6) for n in smoothing_frames}
        smoothed_legal = {
            n: round(op_legal_fpr**n, 6) if op_legal_fpr else 0.0 for n in smoothing_frames
        }
        summary["temporal_smoothing_fpr"] = smoothed_fpr
        summary["temporal_smoothing_legal_fpr"] = smoothed_legal

    summary["distance_stats"] = {
        "normal_p50": round(float(np.median(normal_dists)), 4),
        "normal_p95": round(float(np.percentile(normal_dists, 95)), 4),
        "legal_p50": round(float(np.median(legal_dists)), 4),
        "legal_p95": round(float(np.percentile(legal_dists, 95)), 4),
        "ood_p50": round(float(np.median(ood_dists)), 4),
        "ood_p5": round(float(np.percentile(ood_dists, 5)), 4),
        "separation_gap": round(separation, 4),
    }

    # Per-scenario breakdown at operational threshold
    if op_threshold is not None:
        scenario_detection: dict[str, dict] = {}
        samples_per = max(1, eval_samples // len(OOD_SCENARIOS))
        rng_scenario = np.random.default_rng(seed + 1000)
        for scenario in OOD_SCENARIOS:
            scene_obs = [_ood_obs_tagged(scenario, rng_scenario) for _ in range(samples_per)]
            det_rate = _eval_at_threshold(guard, scene_obs, op_threshold)
            scenario_detection[scenario] = {
                "detection_rate": round(det_rate, 4),
                "samples": samples_per,
            }
            print(f"    {scenario:<25} detection={det_rate:.1%}")
        summary["per_scenario_detection"] = scenario_detection

    verdict = "PASS — deployable" if deployable else "FAIL — not deployable"
    print(f"  Verdict: {verdict}")
    return rows, summary


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV saved: {path}")


def plot_results(rows: list[dict], outdir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot generation.")
        return

    thresholds = [r["threshold"] for r in rows]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(thresholds, [r["fpr"] for r in rows], "b-o", markersize=3, label="FPR (normal)")
    ax.plot(thresholds, [r["fnr"] for r in rows], "r-s", markersize=3, label="FNR (OOD)")
    ax.plot(
        thresholds,
        [r["legal_variation_fpr"] for r in rows],
        "g--^",
        markersize=3,
        label="FPR (legal variation)",
    )
    ax.set_xlabel("nn_threshold")
    ax.set_ylabel("Error Rate")
    ax.set_title("RQ1 — L0 OOD Calibration (Real Guard Pipeline)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_ylim(-0.02, 1.02)
    fig.tight_layout()
    out = outdir / "l0_calibration.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Plot saved: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM L0 OOD Calibration (RQ1)")
    parser.add_argument("--train-samples", type=int, default=200)
    parser.add_argument("--eval-samples", type=int, default=120)
    parser.add_argument("--n-thresholds", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/l0_calibration")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows, summary = run_calibration(
        args.train_samples, args.eval_samples, args.n_thresholds, args.seed
    )
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)
    print(f"\nSummary: {summary}")


if __name__ == "__main__":
    main()
