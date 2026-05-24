"""Experiment — L0 Real-NVP NLL Comparison (RQ1).

Trains a real L0 OODGuard with the ``normalizing_flow`` (Real-NVP) backend on
normal SO-ARM observations, then feeds three held-out observation sequences into
the trained model and records per-frame Negative Log-Likelihood (NLL):

* normal test set: ``MikeChenYZ/soarm-fmb-v2``
* legal-variation test set: ``MikeChenYZ/eval_soarm_fmb``
* abnormal-A test set: ``MikeChenYZ/soarm-recover-failure``

Usage
-----
    python scripts/run_l0_calibration.py \
        --normal-repo MikeChenYZ/soarm-fmb-v2 \
        --legal-repo MikeChenYZ/eval_soarm_fmb \
        --anomaly-repo MikeChenYZ/soarm-recover-failure
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

from dam.guard.builtin.ood import MemoryBank, OODGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision

_DEFAULT_NORMAL_REPO = "MikeChenYZ/soarm-fmb-v2"
_DEFAULT_LEGAL_REPO = "MikeChenYZ/eval_soarm_fmb"
_DEFAULT_ANOMALY_REPO = "MikeChenYZ/soarm-recover-failure"
_DEFAULT_SESSIONS_DIR = "data/robot/sessions"

# Physical limits for OOD scenario generation
_N_JOINTS = 6
_JOINT_UPPER = np.array([1.8243, 1.7691, 1.6026, 1.8067, 3.0741, 1.7453])
_JOINT_LOWER = -_JOINT_UPPER.copy()
_JOINT_LOWER[-1] = 0.0
_JOINT_MID = (_JOINT_UPPER + _JOINT_LOWER) / 2
_JOINT_RANGE = _JOINT_UPPER - _JOINT_LOWER


class DatasetLoadError(RuntimeError):
    """Raised when an RQ1 HuggingFace dataset cannot provide observations."""


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
    max_observations: int | None = None,
    degrees_to_radians: bool = True,
) -> dict[int, list[Observation]]:
    """Load observations from a lerobot HF dataset, grouped by episode.

    Returns {episode_index: [Observation, ...]}.
    """
    import datasets

    try:
        ds = datasets.load_dataset(repo_id, split=split, streaming=True)
    except Exception as exc:
        if exc.__class__.__name__ == "EmptyDatasetError":
            raise DatasetLoadError(
                f"HF dataset repo {repo_id!r} contains no data files for split {split!r}. "
                "RQ1 needs a lerobot dataset with an observation.state column; upload/export "
                "the abnormal-A frames or pass --anomaly-repo to a repo that contains data."
            ) from exc
        raise

    by_episode: dict[int, list[Observation]] = {}
    n_loaded = 0
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
        n_loaded += 1
        if max_observations is not None and n_loaded >= max_observations:
            break

    if not by_episode:
        raise DatasetLoadError(
            f"HF dataset repo {repo_id!r} loaded but produced no observation.state frames "
            f"for split {split!r}."
        )

    return by_episode


# ── Legacy OOD scenario generators (kept for compatibility helpers) ─────────


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


# ── OOD score helpers ────────────────────────────────────────────────────────


def _flatten_episode_obs(
    by_episode: dict[int, list[Observation]],
) -> list[tuple[int, int, Observation]]:
    rows: list[tuple[int, int, Observation]] = []
    for ep in sorted(by_episode.keys()):
        for frame_idx, obs in enumerate(by_episode[ep]):
            rows.append((ep, frame_idx, obs))
    return rows


def _vectors_for_observations(
    guard: OODGuard, labelled_obs: list[tuple[int, int, Observation]]
) -> np.ndarray:
    vectors = np.stack([guard._extractor.extract(obs) for _, _, obs in labelled_obs], axis=0)
    return vectors


def _real_nvp_nll_for_vectors(guard: OODGuard, vectors: np.ndarray) -> np.ndarray:
    if guard._flow is None or not guard._flow.is_fitted:
        raise RuntimeError("Real-NVP flow was not fitted; install torch and retry RQ1.")
    return guard._flow.neg_log_prob_batch(vectors)


def _memory_bank_scores(train_vectors: np.ndarray, eval_vectors: np.ndarray) -> np.ndarray:
    bank = MemoryBank()
    bank.train(train_vectors)
    return np.array([bank.nearest_distance(z) for z in eval_vectors], dtype=np.float32)


def _welford_scores(train_vectors: np.ndarray, eval_vectors: np.ndarray) -> np.ndarray:
    mean = np.mean(train_vectors, axis=0)
    std = np.std(train_vectors, axis=0, ddof=1) if len(train_vectors) > 1 else np.ones_like(mean)
    std = np.sqrt(np.square(std) + 1e-9)
    return np.max(np.abs(eval_vectors - mean) / std, axis=1).astype(np.float32)


def _summarise_scores(values: np.ndarray) -> dict[str, float | int]:
    if len(values) == 0:
        return {"samples": 0}
    return {
        "samples": int(len(values)),
        "mean": round(float(np.mean(values)), 4),
        "std": round(float(np.std(values)), 4),
        "median": round(float(np.median(values)), 4),
        "p05": round(float(np.percentile(values, 5)), 4),
        "p95": round(float(np.percentile(values, 95)), 4),
        "min": round(float(np.min(values)), 4),
        "max": round(float(np.max(values)), 4),
    }


def _build_nll_rows(
    *,
    method: str,
    score_name: str,
    dataset: str,
    repo_id: str,
    labelled_obs: list[tuple[int, int, Observation]],
    scores: np.ndarray,
    is_nll: bool = False,
) -> list[dict]:
    rows: list[dict] = []
    for (episode_index, frame_index, obs), score in zip(labelled_obs, scores, strict=True):
        score_f = float(score)
        rows.append(
            {
                "method": method,
                "dataset": dataset,
                "repo_id": repo_id,
                "episode_index": episode_index,
                "frame_index": frame_index,
                "timestamp": round(float(obs.timestamp), 6),
                "score_name": score_name,
                "score_value": round(score_f, 6),
                "nll": round(score_f, 6) if is_nll else "",
            }
        )
    return rows


# ── Main calibration ────────────────────────────────────────────────────────


def run_calibration(
    normal_repo_id: str = _DEFAULT_NORMAL_REPO,
    legal_repo_id: str = _DEFAULT_LEGAL_REPO,
    anomaly_repo_id: str = _DEFAULT_ANOMALY_REPO,
    sessions_dir: str | None = None,
    ood_samples_per_scenario: int = 30,
    n_thresholds: int = 40,
    seed: int = 42,
    max_observations_per_dataset: int | None = None,
    flow_epochs: int = 50,
    nll_sigma: float = 3.0,
    compare_ood_methods: bool = False,
) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)
    del ood_samples_per_scenario, n_thresholds, sessions_dir

    print(f"  Loading normal dataset:        {normal_repo_id}")
    normal_by_episode = load_observations_from_hf(
        normal_repo_id, max_observations=max_observations_per_dataset
    )
    print(f"  Loading legal-variation set:   {legal_repo_id}")
    legal_by_episode = load_observations_from_hf(
        legal_repo_id, max_observations=max_observations_per_dataset
    )
    print(f"  Loading abnormal-A dataset:    {anomaly_repo_id}")
    anomaly_by_episode = load_observations_from_hf(
        anomaly_repo_id, max_observations=max_observations_per_dataset
    )

    episode_ids = sorted(normal_by_episode.keys())
    if not episode_ids:
        raise RuntimeError(f"No observations loaded from normal repo {normal_repo_id}")
    if not legal_by_episode:
        raise RuntimeError(f"No observations loaded from legal-variation repo {legal_repo_id}")
    if not anomaly_by_episode:
        raise RuntimeError(f"No observations loaded from abnormal-A repo {anomaly_repo_id}")

    n_train = max(1, int(len(episode_ids) * 0.70))
    train_eps = episode_ids[:n_train]
    normal_test_eps = episode_ids[n_train:]
    train_obs = [obs for ep in train_eps for obs in normal_by_episode[ep]]
    normal_labelled = [
        (ep, frame_idx, obs)
        for ep in normal_test_eps
        for frame_idx, obs in enumerate(normal_by_episode[ep])
    ]
    if not normal_labelled:
        sampled = rng.choice(len(train_obs), size=min(200, len(train_obs)), replace=False)
        normal_labelled = [(-1, int(i), train_obs[int(i)]) for i in sampled]

    legal_labelled = _flatten_episode_obs(legal_by_episode)
    anomaly_labelled = _flatten_episode_obs(anomaly_by_episode)

    print(
        f"  Split: train={len(train_obs)}, normal_test={len(normal_labelled)}, "
        f"legal_variation={len(legal_labelled)}, abnormal_a={len(anomaly_labelled)}"
    )

    guard = OODGuard(backend="normalizing_flow")
    precompute_injection(guard, {})
    print(
        f"  Training Real-NVP on {len(train_obs)} normal frames for {flow_epochs} epochs...",
        flush=True,
    )
    guard.train(train_obs, flow_epochs=flow_epochs, flow_verbose=True)

    diag = guard.diagnostics()
    print(
        "  Trained Real-NVP: "
        f"mean_train_nll={diag.get('mean_train_nll')} std_train_nll={diag.get('std_train_nll')}"
    )

    print("  Extracting train embeddings...", flush=True)
    train_vectors = np.stack([guard._extractor.extract(obs) for obs in train_obs], axis=0)
    print("  Extracting eval embeddings...", flush=True)
    eval_vectors = {
        "normal_test": _vectors_for_observations(guard, normal_labelled),
        "legal_variation": _vectors_for_observations(guard, legal_labelled),
        "abnormal_a": _vectors_for_observations(guard, anomaly_labelled),
    }
    labelled_sets = {
        "normal_test": (normal_repo_id, normal_labelled),
        "legal_variation": (legal_repo_id, legal_labelled),
        "abnormal_a": (anomaly_repo_id, anomaly_labelled),
    }

    method_scores: dict[str, dict[str, np.ndarray]] = {"real_nvp": {}}
    method_score_names = {"real_nvp": "nll"}
    for dataset, vectors in eval_vectors.items():
        print(f"  Scoring real_nvp/{dataset} ({len(vectors)} frames)...", flush=True)
        method_scores["real_nvp"][dataset] = _real_nvp_nll_for_vectors(guard, vectors)

    if compare_ood_methods:
        method_scores["memory_bank"] = {}
        for dataset, vectors in eval_vectors.items():
            print(f"  Scoring memory_bank/{dataset} ({len(vectors)} frames)...", flush=True)
            method_scores["memory_bank"][dataset] = _memory_bank_scores(train_vectors, vectors)
        method_scores["welford"] = {}
        for dataset, vectors in eval_vectors.items():
            print(f"  Scoring welford/{dataset} ({len(vectors)} frames)...", flush=True)
            method_scores["welford"][dataset] = _welford_scores(train_vectors, vectors)
        method_score_names.update(
            {
                "memory_bank": "nearest_neighbor_distance",
                "welford": "max_z_score",
            }
        )

    normal_nll = method_scores["real_nvp"]["normal_test"]
    legal_nll = method_scores["real_nvp"]["legal_variation"]
    anomaly_nll = method_scores["real_nvp"]["abnormal_a"]

    rows = []
    for method, dataset_scores in method_scores.items():
        for dataset, scores in dataset_scores.items():
            repo_id, labelled_obs = labelled_sets[dataset]
            rows.extend(
                _build_nll_rows(
                    method=method,
                    score_name=method_score_names[method],
                    dataset=dataset,
                    repo_id=repo_id,
                    labelled_obs=labelled_obs,
                    scores=scores,
                    is_nll=method == "real_nvp",
                )
            )

    train_mean = float(diag.get("mean_train_nll") or 0.0)
    train_std = float(diag.get("std_train_nll") or 0.0)
    nll_threshold = train_mean + nll_sigma * train_std
    normal_fpr = float(np.mean(normal_nll > nll_threshold))
    legal_fpr = float(np.mean(legal_nll > nll_threshold))
    anomaly_detection_rate = float(np.mean(anomaly_nll > nll_threshold))
    anomaly_fnr = 1.0 - anomaly_detection_rate

    stats = {
        "normal_test": {"repo_id": normal_repo_id, **_summarise_scores(normal_nll)},
        "legal_variation": {"repo_id": legal_repo_id, **_summarise_scores(legal_nll)},
        "abnormal_a": {"repo_id": anomaly_repo_id, **_summarise_scores(anomaly_nll)},
    }
    method_stats = {
        method: {
            dataset: {
                "repo_id": labelled_sets[dataset][0],
                "score_name": method_score_names[method],
                **_summarise_scores(scores),
            }
            for dataset, scores in dataset_scores.items()
        }
        for method, dataset_scores in method_scores.items()
    }

    for name, values in stats.items():
        print(
            f"  {name:<16} samples={values['samples']} "
            f"median_nll={values.get('median')} p95_nll={values.get('p95')}"
        )

    summary: dict = {
        "backend": "normalizing_flow",
        "model": "Real-NVP",
        "normal_repo_id": normal_repo_id,
        "legal_repo_id": legal_repo_id,
        "anomaly_repo_id": anomaly_repo_id,
        "train_mean_nll": round(train_mean, 4),
        "train_std_nll": round(train_std, 4),
        "nll_sigma": nll_sigma,
        "nll_threshold": round(nll_threshold, 4),
        "compare_ood_methods": compare_ood_methods,
        "normal_fpr_at_threshold": round(normal_fpr, 4),
        "legal_variation_fpr_at_threshold": round(legal_fpr, 4),
        "abnormal_a_detection_rate_at_threshold": round(anomaly_detection_rate, 4),
        "abnormal_a_fnr_at_threshold": round(anomaly_fnr, 4),
        "dataset_stats": stats,
        "method_stats": method_stats,
        "data_source": "huggingface",
        "train_observations": len(train_obs),
        "normal_test_observations": len(normal_labelled),
        "legal_variation_observations": len(legal_labelled),
        "abnormal_a_observations": len(anomaly_labelled),
    }

    print(f"\n  NLL threshold = {nll_threshold:.4f} (train mean + {nll_sigma:g}σ)")
    print(
        f"  FPR normal={normal_fpr:.3f} legal={legal_fpr:.3f} "
        f"abnormal-A detection={anomaly_detection_rate:.3f}"
    )
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

    fig, ax = plt.subplots(figsize=(8, 5))
    real_nvp_rows = [r for r in rows if r.get("method", "real_nvp") == "real_nvp"]
    groups = {
        name: [float(r["score_value"]) for r in real_nvp_rows if r["dataset"] == name]
        for name in ("normal_test", "legal_variation", "abnormal_a")
    }
    labels = [name for name, values in groups.items() if values]
    data = [groups[name] for name in labels]
    ax.boxplot(data, labels=labels, showfliers=False)
    ax.set_xlabel("Dataset")
    ax.set_ylabel("Per-frame NLL")
    ax.set_title("RQ1 — L0 Real-NVP NLL by Dataset")
    ax.grid(True, alpha=0.3)
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
        default=None,
        help="Deprecated alias for --normal-repo",
    )
    parser.add_argument(
        "--normal-repo",
        type=str,
        default=_DEFAULT_NORMAL_REPO,
        help="Normal HuggingFace lerobot dataset repo id",
    )
    parser.add_argument(
        "--legal-repo",
        type=str,
        default=_DEFAULT_LEGAL_REPO,
        help="Legal-variation HuggingFace lerobot dataset repo id",
    )
    parser.add_argument(
        "--anomaly-repo",
        type=str,
        default=_DEFAULT_ANOMALY_REPO,
        help="Abnormal-A HuggingFace lerobot dataset repo id",
    )
    parser.add_argument(
        "--sessions-dir",
        type=str,
        default=None,
        help="Deprecated; RQ1 now compares HuggingFace test datasets",
    )
    parser.add_argument("--ood-samples", type=int, default=30)
    parser.add_argument("--n-thresholds", type=int, default=40)
    parser.add_argument("--flow-epochs", type=int, default=50)
    parser.add_argument("--nll-sigma", type=float, default=3.0)
    parser.add_argument(
        "--compare-ood-methods",
        action="store_true",
        help="Also score Welford and MemoryBank alongside the default Real-NVP NLL.",
    )
    parser.add_argument("--max-observations-per-dataset", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/l0_calibration")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    normal_repo = args.hf_repo or args.normal_repo
    rows, summary = run_calibration(
        normal_repo_id=normal_repo,
        legal_repo_id=args.legal_repo,
        anomaly_repo_id=args.anomaly_repo,
        sessions_dir=args.sessions_dir,
        ood_samples_per_scenario=args.ood_samples,
        n_thresholds=args.n_thresholds,
        seed=args.seed,
        max_observations_per_dataset=args.max_observations_per_dataset,
        flow_epochs=args.flow_epochs,
        nll_sigma=args.nll_sigma,
        compare_ood_methods=args.compare_ood_methods,
    )
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)
    print(f"\nSummary: {summary}")


if __name__ == "__main__":
    main()
