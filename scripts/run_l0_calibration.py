"""Experiment — L0 OOD Calibration (RQ1).

Trains a real OODGuard (memory_bank backend) on a **HuggingFace lerobot
dataset** (default: ``MikeChenYZ/soarm-fmb-v2``), then evaluates FPR on
held-out episodes and FNR on simulated failure modes.

Optionally loads MCAP session files as additional cross-domain eval data
(``--sessions-dir``).

Data source
-----------
Training & normal-eval observations come from the HF dataset's
``observation.state`` field, split by episode index.

OOD observations use simulated failure modes (sensor fault, joint jam, etc.)
because real OOD events are — by definition — not available in normal recordings.

Usage
-----
    python scripts/run_l0_calibration.py [--hf-repo MikeChenYZ/soarm-fmb-v2]
                                          [--sessions-dir PATH] [--seed S]
                                          [--outdir PATH]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from dam.guard.builtin.ood import OODGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision

_DEFAULT_HF_REPO = "MikeChenYZ/soarm-fmb-v2"
_DEFAULT_SESSIONS_DIR = "data/robot/sessions"

# Physical limits for OOD scenario generation
_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_JOINT_MID = (_JOINT_UPPER + _JOINT_LOWER) / 2
_JOINT_RANGE = _JOINT_UPPER - _JOINT_LOWER


# ── MCAP loader ──────────────────────────────────────────────────────────────


def load_observations_from_mcap(mcap_path: str) -> list[Observation]:
    import msgpack
    from mcap.reader import make_reader

    obs_list: list[Observation] = []
    with open(mcap_path, "rb") as fp:
        reader = make_reader(fp)
        for _schema, _ch, msg in reader.iter_messages(topics=["/dam/cycle"]):
            data = msgpack.unpackb(msg.data, raw=False)
            joint_pos = data.get("obs_joint_positions")
            if joint_pos is None:
                continue
            obs_list.append(
                Observation(
                    timestamp=data.get("obs_timestamp", 0.0),
                    joint_positions=np.array(joint_pos, dtype=np.float64),
                )
            )
    return obs_list


def load_all_sessions(
    sessions_dir: str,
) -> list[tuple[str, list[Observation]]]:
    mcap_files = sorted(f for f in os.listdir(sessions_dir) if f.endswith(".mcap"))
    sessions = []
    for f in mcap_files:
        path = os.path.join(sessions_dir, f)
        obs = load_observations_from_mcap(path)
        if obs:
            sessions.append((f, obs))
    return sessions


# ── HuggingFace dataset loader ──────────────────────────────────────────────


def load_observations_from_hf(
    repo_id: str,
    split: str = "train",
    max_episodes: int | None = None,
    degrees_to_radians: bool = True,
) -> dict[int, list[Observation]]:
    """Load observations from a lerobot HF dataset, grouped by episode.

    Returns {episode_index: [Observation, ...]}.
    """
    import datasets

    ds = datasets.load_dataset(repo_id, split=split, streaming=True)

    by_episode: dict[int, list[Observation]] = {}
    for item in ds:
        ep = item.get("episode_index", 0)
        if max_episodes is not None and ep >= max_episodes:
            break
        state = item.get("observation.state")
        if state is None:
            continue
        positions = np.array(state, dtype=np.float64)
        if degrees_to_radians:
            positions = np.deg2rad(positions)
        obs = Observation(
            timestamp=item.get("timestamp", 0.0),
            joint_positions=positions,
        )
        by_episode.setdefault(ep, []).append(obs)

    return by_episode


# ── OOD scenario generators (simulated failures — not synthetic normals) ─────


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


# ── Evaluation helpers ───────────────────────────────────────────────────────


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


# ── Main calibration ────────────────────────────────────────────────────────


def run_calibration(
    hf_repo_id: str = _DEFAULT_HF_REPO,
    sessions_dir: str | None = _DEFAULT_SESSIONS_DIR,
    ood_samples_per_scenario: int = 30,
    n_thresholds: int = 40,
    seed: int = 42,
) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)

    # Try HF dataset first; fall back to MCAP if `datasets` not installed
    hf_loaded = False
    by_episode: dict[int, list[Observation]] = {}
    try:
        print(f"  Loading HuggingFace dataset {hf_repo_id}...")
        by_episode = load_observations_from_hf(hf_repo_id)
        hf_loaded = bool(by_episode)
    except ImportError:
        print("  `datasets` package not installed — falling back to MCAP data")

    mcap_eval: list[Observation] = []
    mcap_sessions_count = 0

    if hf_loaded:
        episode_ids = sorted(by_episode.keys())
        total_obs = sum(len(v) for v in by_episode.values())
        print(f"  Found {len(episode_ids)} episodes, {total_obs} total observations")

        n_train = max(1, int(len(episode_ids) * 0.70))
        n_eval = max(1, (len(episode_ids) - n_train) // 2)

        train_eps = episode_ids[:n_train]
        eval_eps_a = episode_ids[n_train : n_train + n_eval]
        eval_eps_b = episode_ids[n_train + n_eval :]

        train_obs = [obs for ep in train_eps for obs in by_episode[ep]]
        normal_eval = [obs for ep in eval_eps_a for obs in by_episode[ep]]
        legal_eval = [obs for ep in eval_eps_b for obs in by_episode[ep]]

        if not normal_eval:
            idx = rng.choice(len(train_obs), size=min(200, len(train_obs)), replace=False)
            normal_eval = [train_obs[i] for i in idx]
        if not legal_eval:
            idx = rng.choice(len(train_obs), size=min(200, len(train_obs)), replace=False)
            legal_eval = [train_obs[i] for i in idx]

        # MCAP as cross-domain eval
        if sessions_dir and os.path.isdir(sessions_dir):
            mcap_sessions = load_all_sessions(sessions_dir)
            if mcap_sessions:
                mcap_eval = [obs for _, session_obs in mcap_sessions for obs in session_obs]
                mcap_sessions_count = len(mcap_sessions)
                print(
                    f"  MCAP cross-domain eval: {mcap_sessions_count} sessions, "
                    f"{len(mcap_eval)} observations"
                )
        data_source = "huggingface"
        data_detail = {"hf_repo_id": hf_repo_id, "episodes_used": len(episode_ids)}
    else:
        # Fallback: load MCAP sessions as primary data
        if not sessions_dir or not os.path.isdir(sessions_dir):
            raise RuntimeError(f"HF dataset not available and no MCAP sessions in {sessions_dir}")
        print(f"  Loading MCAP sessions from {sessions_dir}...")
        sessions = load_all_sessions(sessions_dir)
        if not sessions:
            raise RuntimeError(f"No MCAP sessions found in {sessions_dir}")

        total_obs = sum(len(obs) for _, obs in sessions)
        print(f"  Found {len(sessions)} sessions, {total_obs} total observations")

        n_train = max(1, int(len(sessions) * 0.70))
        n_eval = max(1, (len(sessions) - n_train) // 2)
        train_sessions = sessions[:n_train]
        eval_sessions_a = sessions[n_train : n_train + n_eval]
        eval_sessions_b = sessions[n_train + n_eval :]

        train_obs = [obs for _, so in train_sessions for obs in so]
        normal_eval = [obs for _, so in eval_sessions_a for obs in so]
        legal_eval = [obs for _, so in eval_sessions_b for obs in so]

        if not normal_eval:
            idx = rng.choice(len(train_obs), size=min(100, len(train_obs)), replace=False)
            normal_eval = [train_obs[i] for i in idx]
        if not legal_eval:
            idx = rng.choice(len(train_obs), size=min(100, len(train_obs)), replace=False)
            legal_eval = [train_obs[i] for i in idx]

        data_source = "mcap"
        data_detail = {"sessions_used": len(sessions)}

    # OOD: simulated failure modes
    ood_eval: list[Observation] = []
    for scenario in OOD_SCENARIOS:
        for _ in range(ood_samples_per_scenario):
            ood_eval.append(_ood_obs_tagged(scenario, rng))

    print(
        f"  Split: train={len(train_obs)}, "
        f"normal_eval={len(normal_eval)}, legal_eval={len(legal_eval)}, "
        f"ood_eval={len(ood_eval)} (source: {data_source})"
    )

    # Train
    guard = OODGuard(backend="memory_bank")
    precompute_injection(guard, {})
    guard.train(train_obs)

    diag = guard.diagnostics()
    print(f"  Trained: bank_size={diag['bank_size']}, backend={diag['bank_backend']}")

    # Distance distributions
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

    # Threshold sweep
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

    summary: dict = {
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
        "data_source": data_source,
        **data_detail,
        "train_observations": len(train_obs),
        "eval_observations": len(normal_eval),
        "ood_observations": len(ood_eval),
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

    # Temporal smoothing
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

    # Per-scenario breakdown
    if op_threshold is not None:
        scenario_detection: dict[str, dict] = {}
        rng_scenario = np.random.default_rng(seed + 1000)
        for scenario in OOD_SCENARIOS:
            scene_obs = [
                _ood_obs_tagged(scenario, rng_scenario) for _ in range(ood_samples_per_scenario)
            ]
            det_rate = _eval_at_threshold(guard, scene_obs, op_threshold)
            scenario_detection[scenario] = {
                "detection_rate": round(det_rate, 4),
                "samples": ood_samples_per_scenario,
            }
            print(f"    {scenario:<25} detection={det_rate:.1%}")
        summary["per_scenario_detection"] = scenario_detection

    # MCAP cross-domain eval
    if mcap_eval and op_threshold is not None:
        mcap_fpr = _eval_at_threshold(guard, mcap_eval, op_threshold)
        summary["mcap_cross_domain"] = {
            "sessions": mcap_sessions_count,
            "observations": len(mcap_eval),
            "fpr_at_operational": round(mcap_fpr, 4),
        }
        print(
            f"  MCAP cross-domain FPR at operational threshold: {mcap_fpr:.1%} "
            f"({len(mcap_eval)} obs from {mcap_sessions_count} sessions)"
        )

    # Recommendations
    recommendations: list[str] = []
    if not deployable:
        if op_detection_rate < 0.80:
            recommendations.append(
                "Train feature extractor on this data to improve embedding separation."
            )
        if separation < 0:
            recommendations.append(
                "Enable temporal_smoothing_frames >= 3 to suppress "
                f"single-frame false positives (legal FPR drops to "
                f"{summary.get('temporal_smoothing_legal_fpr', {}).get(3, '?')})."
            )
    if op_threshold is not None and "per_scenario_detection" in summary:
        weak = [
            s for s, d in summary["per_scenario_detection"].items() if d["detection_rate"] < 0.70
        ]
        if weak:
            recommendations.append(
                f"Scenarios [{', '.join(weak)}] need L1 motion guard "
                "cooperation — L0 alone cannot catch physically valid postures."
            )
    summary["recommendations"] = recommendations

    verdict = "PASS — deployable" if deployable else "FAIL — not deployable"
    print(f"  Verdict: {verdict}")
    if recommendations:
        for r in recommendations:
            print(f"  → {r}")
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
    ax.set_title("RQ1 — L0 OOD Calibration (HuggingFace lerobot)")
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
    parser.add_argument(
        "--hf-repo",
        type=str,
        default=_DEFAULT_HF_REPO,
        help="HuggingFace lerobot dataset repo id",
    )
    parser.add_argument(
        "--sessions-dir",
        type=str,
        default=_DEFAULT_SESSIONS_DIR,
        help="Path to MCAP session files (optional cross-domain eval)",
    )
    parser.add_argument("--ood-samples", type=int, default=30)
    parser.add_argument("--n-thresholds", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/l0_calibration")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows, summary = run_calibration(
        args.hf_repo, args.sessions_dir, args.ood_samples, args.n_thresholds, args.seed
    )
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)
    print(f"\nSummary: {summary}")


if __name__ == "__main__":
    main()
