"""Experiment — L0 ``ood_detector`` Real-NVP NLL Comparison (RQ1).

Fits the Real-NVP backend used by the runtime ``ood_detector`` boundary on
normal SO-ARM observations, then feeds three held-out observation sequences
through the same feature/backend contract and records per-frame NLL:

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
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from dam.guard.builtin.ood import OODGuard
from dam.guard.ood_backend import MemoryBankBackend, OODBackend, RealNVPFlowBackend, WelfordBackend
from dam.guard.ood_context import OODContext
from dam.types.observation import Observation
from dam.types.result import GuardDecision
from scripts._experiment_logging import configure_cli_logging

LOGGER = logging.getLogger(__name__)

_DEFAULT_NORMAL_REPO = "MikeChenYZ/soarm-fmb-v2"
_DEFAULT_LEGAL_REPO = "MikeChenYZ/eval_soarm_fmb"
_DEFAULT_ANOMALY_REPO = "MikeChenYZ/soarm-recover-failure"
_DEFAULT_SESSIONS_DIR = "data/robot/sessions"
_DEFAULT_CACHE_DIR = "data/experiments/l0_calibration/cache"
_DEFAULT_RUNTIME_MODEL_PATH = "data/ood_models/ood_model.pt"
_FEATURE_CACHE_VERSION = 4

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


def _cache_key(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _dataset_cache_path(
    cache_dir: str | Path,
    *,
    repo_id: str,
    split: str,
    max_observations: int | None,
    degrees_to_radians: bool,
) -> Path:
    key = _cache_key(
        {
            "kind": "hf_observations",
            "repo_id": repo_id,
            "split": split,
            "max_observations": max_observations,
            "degrees_to_radians": degrees_to_radians,
        }
    )
    return Path(cache_dir) / "datasets" / f"{key}.npz"


def _save_observation_cache(path: Path, by_episode: dict[int, list[Observation]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    episode_indices: list[int] = []
    frame_indices: list[int] = []
    timestamps: list[float] = []
    positions: list[np.ndarray] = []
    for ep in sorted(by_episode.keys()):
        for frame_idx, obs in enumerate(by_episode[ep]):
            episode_indices.append(ep)
            frame_indices.append(frame_idx)
            timestamps.append(float(obs.timestamp))
            positions.append(np.asarray(obs.joint_positions, dtype=np.float64))
    np.savez_compressed(
        path,
        episode_indices=np.asarray(episode_indices, dtype=np.int64),
        frame_indices=np.asarray(frame_indices, dtype=np.int64),
        timestamps=np.asarray(timestamps, dtype=np.float64),
        positions=np.stack(positions, axis=0),
    )


def _load_observation_cache(path: Path) -> dict[int, list[Observation]]:
    data = np.load(path)
    by_episode: dict[int, list[Observation]] = {}
    for ep, ts, pos in zip(
        data["episode_indices"],
        data["timestamps"],
        data["positions"],
        strict=True,
    ):
        by_episode.setdefault(int(ep), []).append(
            Observation(timestamp=float(ts), joint_positions=np.asarray(pos, dtype=np.float64))
        )
    if not by_episode:
        raise DatasetLoadError(f"Observation cache {path} is empty.")
    return by_episode


def load_observations_from_hf_cached(
    repo_id: str,
    *,
    cache_dir: str | Path | None,
    split: str = "train",
    max_observations: int | None = None,
    degrees_to_radians: bool = True,
) -> dict[int, list[Observation]]:
    if cache_dir:
        path = _dataset_cache_path(
            cache_dir,
            repo_id=repo_id,
            split=split,
            max_observations=max_observations,
            degrees_to_radians=degrees_to_radians,
        )
        if path.is_file():
            LOGGER.info("Dataset cache hit: %s -> %s", repo_id, path)
            return _load_observation_cache(path)

    by_episode = load_observations_from_hf(
        repo_id,
        split=split,
        max_observations=max_observations,
        degrees_to_radians=degrees_to_radians,
    )
    if cache_dir:
        _save_observation_cache(path, by_episode)
        LOGGER.info("Dataset cache saved: %s -> %s", repo_id, path)
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
    context: OODContext, labelled_obs: list[tuple[int, int, Observation]]
) -> np.ndarray:
    return context.features_batch([obs for _, _, obs in labelled_obs])


def _score_backend(backend: OODBackend, vectors: np.ndarray) -> np.ndarray:
    return np.asarray([backend.score(vector) for vector in vectors], dtype=np.float32)


def _real_nvp_nll_for_vectors(backend: RealNVPFlowBackend, vectors: np.ndarray) -> np.ndarray:
    if not backend.is_ready():
        raise RuntimeError("Real-NVP flow was not fitted; install torch and retry RQ1.")
    return _score_backend(backend, vectors)


def _memory_bank_scores(train_vectors: np.ndarray, eval_vectors: np.ndarray) -> np.ndarray:
    backend = MemoryBankBackend()
    backend.train(train_vectors)
    return _score_backend(backend, eval_vectors)


def _welford_scores(train_vectors: np.ndarray, eval_vectors: np.ndarray) -> np.ndarray:
    backend = WelfordBackend()
    backend.train(train_vectors)
    return _score_backend(backend, eval_vectors)


def _temporal_sequence_scores(
    labelled_obs: list[tuple[int, int, Observation]],
) -> np.ndarray:
    """Return per-frame joint speed magnitude, resetting at episode boundaries.

    This is an explicit trajectory diagnostic and is not silently appended to
    the Real-NVP feature vector. A frame-level OOD score and a motion jump are
    distinct measurements and should remain distinguishable in RQ1 output.
    """
    scores = np.zeros(len(labelled_obs), dtype=np.float32)
    previous: tuple[int, Observation] | None = None
    for index, (episode_index, _frame_index, obs) in enumerate(labelled_obs):
        if previous is not None and previous[0] == episode_index:
            dt = float(obs.timestamp) - float(previous[1].timestamp)
            if dt > 0:
                delta = np.asarray(obs.joint_positions) - np.asarray(previous[1].joint_positions)
                scores[index] = float(np.linalg.norm(delta) / dt)
        previous = (episode_index, obs)
    return scores


def _vectors_fingerprint(vectors: np.ndarray) -> str:
    if len(vectors) == 0:
        return "empty"
    sample_indices = sorted({0, len(vectors) // 2, len(vectors) - 1})
    sample = np.ascontiguousarray(vectors[sample_indices].astype(np.float32))
    digest = hashlib.sha256()
    digest.update(str(vectors.shape).encode("utf-8"))
    digest.update(sample.tobytes())
    return digest.hexdigest()[:16]


def _flow_cache_path(
    cache_dir: str | Path,
    *,
    normal_repo_id: str,
    max_observations_per_dataset: int | None,
    flow_epochs: int,
    train_vectors: np.ndarray,
    feature_config: dict[str, object],
) -> Path:
    key = _cache_key(
        {
            "kind": "real_nvp_flow",
            "normal_repo_id": normal_repo_id,
            "max_observations_per_dataset": max_observations_per_dataset,
            "flow_epochs": flow_epochs,
            "feature_config": feature_config,
            "train_shape": train_vectors.shape,
            "train_fingerprint": _vectors_fingerprint(train_vectors),
        }
    )
    return Path(cache_dir) / "models" / f"{key}.flow.pt"


def _feature_cache_path(
    cache_dir: str | Path,
    *,
    normal_repo_id: str,
    legal_repo_id: str,
    anomaly_repo_id: str,
    max_observations_per_dataset: int | None,
    feature_config: dict[str, object],
) -> Path:
    key = _cache_key(
        {
            "kind": "rq1_features",
            "version": _FEATURE_CACHE_VERSION,
            "normal_repo_id": normal_repo_id,
            "legal_repo_id": legal_repo_id,
            "anomaly_repo_id": anomaly_repo_id,
            "max_observations_per_dataset": max_observations_per_dataset,
            "feature_config": feature_config,
        }
    )
    return Path(cache_dir) / "features" / f"{key}.npz"


def _feature_extractor_cache_path(feature_path: Path) -> Path:
    return feature_path.with_suffix(".extractor.pt")


def _feature_cache_ready_for_run(feature_path: Path | None, runtime_model_path: str | None) -> bool:
    if feature_path is None or not feature_path.is_file():
        return False
    return not runtime_model_path or _feature_extractor_cache_path(feature_path).is_file()


def _save_feature_cache(
    path: Path,
    *,
    train_vectors: np.ndarray,
    eval_vectors: dict[str, np.ndarray],
    kept_indices: dict[str, np.ndarray],
    vision_frames: dict[str, int] | None,
    vision_candidates: dict[str, int] | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        train_vectors=train_vectors,
        normal_test_vectors=eval_vectors["normal_test"],
        legal_variation_vectors=eval_vectors["legal_variation"],
        abnormal_a_vectors=eval_vectors["abnormal_a"],
        train_indices=kept_indices["train"],
        normal_test_indices=kept_indices["normal_test"],
        legal_variation_indices=kept_indices["legal_variation"],
        abnormal_a_indices=kept_indices["abnormal_a"],
        vision_frames=json.dumps(vision_frames),
        vision_candidates=json.dumps(vision_candidates),
    )


def _load_feature_cache(
    path: Path,
) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray], dict | None, dict | None]:
    data = np.load(path)
    return (
        data["train_vectors"],
        {
            "normal_test": data["normal_test_vectors"],
            "legal_variation": data["legal_variation_vectors"],
            "abnormal_a": data["abnormal_a_vectors"],
        },
        {
            "train": data["train_indices"],
            "normal_test": data["normal_test_indices"],
            "legal_variation": data["legal_variation_indices"],
            "abnormal_a": data["abnormal_a_indices"],
        },
        json.loads(str(data["vision_frames"])),
        json.loads(str(data["vision_candidates"])),
    )


def _flow_path_for_runtime_model(model_path: Path) -> Path:
    if model_path.suffix == ".pt":
        return model_path.with_name(f"{model_path.stem}_flow.pt")
    return Path(f"{model_path}_flow.pt")


def _export_runtime_bundle(
    *,
    backend: RealNVPFlowBackend,
    runtime_model_path: str | Path,
    extractor: object | None,
    extractor_source_path: Path | None,
    eer_threshold: float,
    feature_config: dict[str, object],
    normal_repo_id: str,
    legal_repo_id: str,
    anomaly_repo_id: str,
) -> dict[str, object]:
    model_path = Path(runtime_model_path)
    flow_path = _flow_path_for_runtime_model(model_path)
    metadata_path = model_path.with_suffix(".json")
    model_path.parent.mkdir(parents=True, exist_ok=True)

    if extractor_source_path is not None and extractor_source_path.is_file():
        shutil.copy2(extractor_source_path, model_path)
    elif extractor is not None:
        extractor.save(str(model_path))  # type: ignore[attr-defined]
    else:
        raise RuntimeError("Runtime export requires the cached feature extractor sidecar.")
    if not model_path.is_file():
        raise RuntimeError("Runtime export requires a persisted feature extractor checkpoint.")

    backend.save(flow_path)
    if not flow_path.is_file():
        raise RuntimeError("Runtime export requires a persisted Real-NVP flow checkpoint.")

    stackfile_params: dict[str, object] = {
        "backend": "normalizing_flow",
        "ood_model_path": str(model_path),
        "nll_sigma": 0,
        "nll_threshold": round(float(eer_threshold), 4),
    }
    if feature_config.get("vision_model"):
        stackfile_params.update(
            {
                "vision_model": feature_config["vision_model"],
                "vision_weight": feature_config["vision_weight"],
            }
        )
        if feature_config.get("vision_camera"):
            stackfile_params["vision_camera"] = feature_config["vision_camera"]
    metadata = {
        "callback": "ood_detector",
        "backend": "normalizing_flow",
        "flow_path": str(flow_path),
        "feature_config": feature_config,
        "normal_repo_id": normal_repo_id,
        "legal_repo_id": legal_repo_id,
        "anomaly_repo_id": anomaly_repo_id,
        "stackfile_params": stackfile_params,
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    LOGGER.info(
        "Runtime OOD bundle exported: model=%s flow=%s metadata=%s",
        model_path,
        flow_path,
        metadata_path,
    )
    return {
        "runtime_model_path": str(model_path),
        "runtime_flow_path": str(flow_path),
        "runtime_metadata_path": str(metadata_path),
        "stackfile_params": stackfile_params,
    }


def _fit_or_load_real_nvp(
    backend: RealNVPFlowBackend,
    train_vectors: np.ndarray,
    *,
    cache_dir: str | Path | None,
    normal_repo_id: str,
    max_observations_per_dataset: int | None,
    flow_epochs: int,
    feature_config: dict[str, object],
) -> tuple[float, float, bool]:
    flow_path: Path | None = None
    if cache_dir:
        flow_path = _flow_cache_path(
            cache_dir,
            normal_repo_id=normal_repo_id,
            max_observations_per_dataset=max_observations_per_dataset,
            flow_epochs=flow_epochs,
            train_vectors=train_vectors,
            feature_config=feature_config,
        )
        if flow_path.is_file():
            LOGGER.info("Real-NVP model cache hit: %s", flow_path)
            backend.load(flow_path)
            return (
                float(backend.mean_train_nll or 0.0),
                float(backend.std_train_nll or 0.0),
                True,
            )

    LOGGER.info(
        "Training Real-NVP on %d normal frames for %d epochs...",
        len(train_vectors),
        flow_epochs,
    )
    backend.train(train_vectors, epochs=flow_epochs, verbose=True)
    mean_nll = float(backend.mean_train_nll or 0.0)
    std_nll = float(backend.std_train_nll or 0.0)
    if flow_path is not None:
        flow_path.parent.mkdir(parents=True, exist_ok=True)
        backend.save(flow_path)
        LOGGER.info("Real-NVP model cache saved: %s", flow_path)
    return mean_nll, std_nll, False


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


def _compute_roc_and_eer(
    normal_scores: np.ndarray,
    anomaly_scores: np.ndarray,
    n_thresholds: int = 500,
) -> dict:
    """Compute ROC curve, AUROC, and EER threshold from normal vs anomaly scores.

    Higher score → more likely OOD (anomalous).
    Returns dict with fpr, tpr, auroc, eer, eer_threshold.
    """
    all_scores = np.concatenate([normal_scores, anomaly_scores])
    thresholds = np.linspace(float(all_scores.min()), float(all_scores.max()), n_thresholds)

    fpr_list = []
    tpr_list = []
    for t in thresholds:
        fpr_list.append(float(np.mean(normal_scores > t)))
        tpr_list.append(float(np.mean(anomaly_scores > t)))

    fpr_arr = np.array(fpr_list)
    tpr_arr = np.array(tpr_list)

    # AUROC via trapezoidal integration (FPR descending, TPR descending)
    sorted_idx = np.argsort(fpr_arr)
    auroc = float(np.trapezoid(tpr_arr[sorted_idx], fpr_arr[sorted_idx]))
    auroc = abs(auroc)

    # EER: where FPR ≈ FNR (i.e., FPR ≈ 1 - TPR)
    fnr_arr = 1.0 - tpr_arr
    eer_idx = int(np.argmin(np.abs(fpr_arr - fnr_arr)))
    eer = float((fpr_arr[eer_idx] + fnr_arr[eer_idx]) / 2.0)
    eer_threshold = float(thresholds[eer_idx])

    # Subsample ROC curve for storage (keep ~50 points)
    step = max(1, n_thresholds // 50)
    return {
        "fpr": fpr_arr[::step].tolist(),
        "tpr": tpr_arr[::step].tolist(),
        "auroc": auroc,
        "eer": eer,
        "eer_threshold": eer_threshold,
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


# ── Vision frame loading helpers ──────────────────────────────────────────────


def _attach_vision_frames(
    repo_id: str,
    episode_ids: list[int],
    by_episode: dict[int, list[Observation]],
    obs_list: list[Observation],
    camera: str,
    subsample: int,
    label: str,
) -> int:
    """Load video frames and attach them to Observation objects in-place.

    Since Observation is frozen, we replace items in obs_list with new instances.
    Only loads every Nth frame (subsample) to keep memory/time reasonable.
    """
    from dam.guard.lerobot_video_loader import LeRobotVideoLoader

    LOGGER.info("Loading %s video frames (%s, camera=%s)...", label, repo_id, camera)
    loader = LeRobotVideoLoader(repo_id, camera=camera)

    obs_idx = 0
    for ep_id in episode_ids:
        ep_obs = by_episode.get(ep_id, [])
        try:
            frames = loader.load_episode_frames(ep_id, subsample=subsample)
        except Exception as exc:
            LOGGER.warning("Could not load video for episode %s: %s", ep_id, exc)
            obs_idx += len(ep_obs)
            continue

        for frame_idx, obs in enumerate(ep_obs):
            if obs_idx >= len(obs_list):
                break
            video_frame_idx = frame_idx // subsample
            if video_frame_idx < len(frames) and frame_idx % subsample == 0:
                new_obs = Observation(
                    timestamp=obs.timestamp,
                    joint_positions=obs.joint_positions,
                    joint_velocities=obs.joint_velocities,
                    end_effector_pose=obs.end_effector_pose,
                    force_torque=obs.force_torque,
                    images={camera: frames[video_frame_idx]},
                    channels=obs.channels,
                    metadata=obs.metadata,
                )
                obs_list[obs_idx] = new_obs
            obs_idx += 1

    n_with_images = sum(1 for o in obs_list if o.images is not None)
    LOGGER.info("Attached images for %s: %d/%d observations", label, n_with_images, len(obs_list))
    return n_with_images


def _attach_vision_frames_labelled(
    repo_id: str,
    episode_ids: list[int],
    by_episode: dict[int, list[Observation]],
    labelled: list[tuple[int, int, Observation]],
    camera: str,
    subsample: int,
    label: str,
) -> int:
    """Attach video frames to labelled observation tuples."""
    from dam.guard.lerobot_video_loader import LeRobotVideoLoader

    LOGGER.info("Loading %s video frames (%s, camera=%s)...", label, repo_id, camera)
    loader = LeRobotVideoLoader(repo_id, camera=camera)

    frames_by_episode: dict[int, list[np.ndarray]] = {}
    for ep_id in episode_ids:
        try:
            frames_by_episode[ep_id] = loader.load_episode_frames(ep_id, subsample=subsample)
        except Exception as exc:
            LOGGER.warning("Could not load video for episode %s: %s", ep_id, exc)

    n_attached = 0
    for i, (ep, frame_idx, obs) in enumerate(labelled):
        frames = frames_by_episode.get(ep)
        if frames is None:
            continue
        video_frame_idx = frame_idx // subsample
        if video_frame_idx < len(frames) and frame_idx % subsample == 0:
            new_obs = Observation(
                timestamp=obs.timestamp,
                joint_positions=obs.joint_positions,
                joint_velocities=obs.joint_velocities,
                end_effector_pose=obs.end_effector_pose,
                force_torque=obs.force_torque,
                images={camera: frames[video_frame_idx]},
                channels=obs.channels,
                metadata=obs.metadata,
            )
            labelled[i] = (ep, frame_idx, new_obs)
            n_attached += 1

    LOGGER.info("Attached images for %s: %d/%d observations", label, n_attached, len(labelled))
    return n_attached


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
    cache_dir: str | None = _DEFAULT_CACHE_DIR,
    vision_model: str | None = None,
    vision_weight: float = 0.3,
    vision_camera: str = "top",
    vision_subsample: int = 30,
    runtime_model_path: str | None = _DEFAULT_RUNTIME_MODEL_PATH,
) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)
    del ood_samples_per_scenario, n_thresholds, sessions_dir

    LOGGER.info("Loading normal dataset: %s", normal_repo_id)
    normal_by_episode = load_observations_from_hf_cached(
        normal_repo_id,
        cache_dir=cache_dir,
        max_observations=max_observations_per_dataset,
    )
    LOGGER.info("Loading legal-variation set: %s", legal_repo_id)
    legal_by_episode = load_observations_from_hf_cached(
        legal_repo_id,
        cache_dir=cache_dir,
        max_observations=max_observations_per_dataset,
    )
    LOGGER.info("Loading abnormal-A dataset: %s", anomaly_repo_id)
    anomaly_by_episode = load_observations_from_hf_cached(
        anomaly_repo_id,
        cache_dir=cache_dir,
        max_observations=max_observations_per_dataset,
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

    LOGGER.info(
        "Dataset split: train=%d normal_test=%d legal_variation=%d abnormal_a=%d",
        len(train_obs),
        len(normal_labelled),
        len(legal_labelled),
        len(anomaly_labelled),
    )

    feature_config: dict[str, object] = {
        "state": "observation.state",
        "feature_seed": seed,
        "vision_model": vision_model,
        "vision_weight": vision_weight if vision_model else None,
        "vision_camera": vision_camera if vision_model else None,
        "vision_subsample": vision_subsample if vision_model else None,
    }
    vision_frames: dict[str, int] | None = None
    vision_candidates: dict[str, int] | None = None
    runtime_extractor: object | None = None
    runtime_extractor_source_path: Path | None = None
    feature_path = (
        _feature_cache_path(
            cache_dir,
            normal_repo_id=normal_repo_id,
            legal_repo_id=legal_repo_id,
            anomaly_repo_id=anomaly_repo_id,
            max_observations_per_dataset=max_observations_per_dataset,
            feature_config=feature_config,
        )
        if cache_dir
        else None
    )
    feature_extractor_path = (
        _feature_extractor_cache_path(feature_path) if feature_path is not None else None
    )
    kept_indices: dict[str, np.ndarray]
    feature_cache_ready = _feature_cache_ready_for_run(feature_path, runtime_model_path)
    if (
        feature_path is not None
        and feature_path.is_file()
        and not feature_cache_ready
        and runtime_model_path
    ):
        LOGGER.info("Embedding cache missing runtime extractor sidecar; refreshing embeddings.")
    if feature_cache_ready:
        assert feature_path is not None
        LOGGER.info("Embedding cache hit: %s", feature_path)
        train_vectors, eval_vectors, kept_indices, vision_frames, vision_candidates = (
            _load_feature_cache(feature_path)
        )
        runtime_extractor_source_path = feature_extractor_path
    else:
        # Vision mode evaluates only observations with an actual video frame. It
        # must not compare image embeddings against zero-padded missing-image rows.
        if vision_model:
            LOGGER.info(
                "Vision model: %s (weight=%s, camera=%s)",
                vision_model,
                vision_weight,
                vision_camera,
            )
            vision_candidates = {
                "train": len(train_obs),
                "normal_test": len(normal_labelled),
                "legal_variation": len(legal_labelled),
                "abnormal_a": len(anomaly_labelled),
            }
            vision_frames = {}
            vision_frames["train"] = _attach_vision_frames(
                normal_repo_id,
                train_eps,
                normal_by_episode,
                train_obs,
                vision_camera,
                vision_subsample,
                "train",
            )
            vision_frames["normal_test"] = _attach_vision_frames_labelled(
                normal_repo_id,
                normal_test_eps,
                normal_by_episode,
                normal_labelled,
                vision_camera,
                vision_subsample,
                "normal_test",
            )
            vision_frames["legal_variation"] = _attach_vision_frames_labelled(
                legal_repo_id,
                sorted(legal_by_episode.keys()),
                legal_by_episode,
                legal_labelled,
                vision_camera,
                vision_subsample,
                "legal_variation",
            )
            vision_frames["abnormal_a"] = _attach_vision_frames_labelled(
                anomaly_repo_id,
                sorted(anomaly_by_episode.keys()),
                anomaly_by_episode,
                anomaly_labelled,
                vision_camera,
                vision_subsample,
                "abnormal_a",
            )

        kept_indices = {
            "train": np.asarray(
                [i for i, obs in enumerate(train_obs) if not vision_model or obs.images]
            ),
            "normal_test": np.asarray(
                [i for i, row in enumerate(normal_labelled) if not vision_model or row[2].images]
            ),
            "legal_variation": np.asarray(
                [i for i, row in enumerate(legal_labelled) if not vision_model or row[2].images]
            ),
            "abnormal_a": np.asarray(
                [i for i, row in enumerate(anomaly_labelled) if not vision_model or row[2].images]
            ),
        }
        if vision_model and any(len(indices) == 0 for indices in kept_indices.values()):
            raise DatasetLoadError(
                "Vision RQ1 requested, but one or more datasets provided no matching video "
                "frames. Check the camera name and the dataset video assets."
            )

        # FeatureExtractor contains an untrained projection; seed it so equal
        # input/configuration maps to one stable feature/model cache identity.
        try:
            import torch

            torch.manual_seed(seed)
        except ImportError:
            pass
        context = OODContext()
        if vision_model:
            context.configure_vision(vision_model, vision_weight, device="cpu")
        runtime_extractor = context.feature_extractor

        scored_train_obs = [train_obs[int(i)] for i in kept_indices["train"]]
        LOGGER.info("Extracting train embeddings...")
        train_vectors = context.features_batch(scored_train_obs)
        scored_labelled = {
            "normal_test": [normal_labelled[int(i)] for i in kept_indices["normal_test"]],
            "legal_variation": [legal_labelled[int(i)] for i in kept_indices["legal_variation"]],
            "abnormal_a": [anomaly_labelled[int(i)] for i in kept_indices["abnormal_a"]],
        }
        LOGGER.info("Extracting eval embeddings...")
        eval_vectors = {
            dataset: _vectors_for_observations(context, labelled)
            for dataset, labelled in scored_labelled.items()
        }
        if feature_path is not None:
            _save_feature_cache(
                feature_path,
                train_vectors=train_vectors,
                eval_vectors=eval_vectors,
                kept_indices=kept_indices,
                vision_frames=vision_frames,
                vision_candidates=vision_candidates,
            )
            assert feature_extractor_path is not None
            context.feature_extractor.save(str(feature_extractor_path))
            runtime_extractor_source_path = feature_extractor_path
            LOGGER.info("Embedding cache saved: %s", feature_path)

    train_obs = [train_obs[int(i)] for i in kept_indices["train"]]
    normal_labelled = [normal_labelled[int(i)] for i in kept_indices["normal_test"]]
    legal_labelled = [legal_labelled[int(i)] for i in kept_indices["legal_variation"]]
    anomaly_labelled = [anomaly_labelled[int(i)] for i in kept_indices["abnormal_a"]]
    if vision_frames:
        LOGGER.info(
            "Vision scoring frames: %s",
            ", ".join(f"{name}={count}" for name, count in vision_frames.items()),
        )

    flow_backend = RealNVPFlowBackend(device="cpu")
    train_mean, train_std, model_cache_hit = _fit_or_load_real_nvp(
        flow_backend,
        train_vectors,
        cache_dir=cache_dir,
        normal_repo_id=normal_repo_id,
        max_observations_per_dataset=max_observations_per_dataset,
        flow_epochs=flow_epochs,
        feature_config=feature_config,
    )
    LOGGER.info(
        "Real-NVP ready: mean_train_nll=%.4f std_train_nll=%.4f source=%s",
        train_mean,
        train_std,
        "cache" if model_cache_hit else "trained",
    )
    labelled_sets = {
        "normal_test": (normal_repo_id, normal_labelled),
        "legal_variation": (legal_repo_id, legal_labelled),
        "abnormal_a": (anomaly_repo_id, anomaly_labelled),
    }

    method_scores: dict[str, dict[str, np.ndarray]] = {"real_nvp": {}}
    method_score_names = {"real_nvp": "nll"}
    for dataset, vectors in eval_vectors.items():
        LOGGER.info("Scoring real_nvp/%s (%d frames)...", dataset, len(vectors))
        method_scores["real_nvp"][dataset] = _real_nvp_nll_for_vectors(flow_backend, vectors)

    if compare_ood_methods:
        method_scores["memory_bank"] = {}
        for dataset, vectors in eval_vectors.items():
            LOGGER.info("Scoring memory_bank/%s (%d frames)...", dataset, len(vectors))
            method_scores["memory_bank"][dataset] = _memory_bank_scores(train_vectors, vectors)
        method_scores["welford"] = {}
        for dataset, vectors in eval_vectors.items():
            LOGGER.info("Scoring welford/%s (%d frames)...", dataset, len(vectors))
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

    # ── ROC / AUROC / EER threshold calibration ────────────────────────────────
    # Use normal_test as negatives (label=0) and abnormal_a as positives (label=1).
    # Higher NLL → more likely OOD. EER is the operating point where FPR == FNR.
    roc_result = _compute_roc_and_eer(normal_nll, anomaly_nll)
    eer_threshold = roc_result["eer_threshold"]
    auroc = roc_result["auroc"]
    eer = roc_result["eer"]

    # Evaluate all datasets at the EER threshold τ*
    normal_fpr = float(np.mean(normal_nll > eer_threshold))
    legal_fpr = float(np.mean(legal_nll > eer_threshold))
    anomaly_detection_rate = float(np.mean(anomaly_nll > eer_threshold))
    anomaly_fnr = 1.0 - anomaly_detection_rate

    # Legacy sigma-based threshold for comparison
    sigma_threshold = train_mean + nll_sigma * train_std

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
    trajectory_stats = {
        dataset: _summarise_scores(_temporal_sequence_scores(labelled_obs))
        for dataset, (_repo_id, labelled_obs) in labelled_sets.items()
    }

    for name, values in stats.items():
        LOGGER.info(
            "%s samples=%s median_nll=%s p95_nll=%s",
            name,
            values["samples"],
            values.get("median"),
            values.get("p95"),
        )

    summary: dict = {
        "callback": "ood_detector",
        "backend": "normalizing_flow",
        "model": "Real-NVP",
        "feature_sources": ["observation.state"]
        + ([f"image:{vision_model}"] if vision_model else []),
        "feature_config": feature_config,
        "not_scored_fields": ["action"],
        "trajectory_diagnostic": "joint_speed_magnitude_only_not_model_input",
        "trajectory_stats": trajectory_stats,
        "vision_model": vision_model,
        "vision_weight": vision_weight if vision_model else None,
        "vision_camera": vision_camera if vision_model else None,
        "vision_subsample": vision_subsample if vision_model else None,
        "vision_frames_attached": vision_frames,
        "vision_candidate_frames": vision_candidates,
        "normal_repo_id": normal_repo_id,
        "legal_repo_id": legal_repo_id,
        "anomaly_repo_id": anomaly_repo_id,
        "train_mean_nll": round(train_mean, 4),
        "train_std_nll": round(train_std, 4),
        "threshold_method": "EER",
        "eer_threshold": round(eer_threshold, 4),
        "eer": round(eer, 4),
        "auroc": round(auroc, 4),
        "roc_fpr": [round(x, 4) for x in roc_result["fpr"]],
        "roc_tpr": [round(x, 4) for x in roc_result["tpr"]],
        "sigma_threshold_legacy": round(sigma_threshold, 4),
        "nll_sigma": nll_sigma,
        "compare_ood_methods": compare_ood_methods,
        "cache_dir": cache_dir,
        "real_nvp_model_cache_hit": model_cache_hit,
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
    if runtime_model_path:
        summary.update(
            _export_runtime_bundle(
                backend=flow_backend,
                runtime_model_path=runtime_model_path,
                extractor=runtime_extractor,
                extractor_source_path=runtime_extractor_source_path,
                eer_threshold=eer_threshold,
                feature_config=feature_config,
                normal_repo_id=normal_repo_id,
                legal_repo_id=legal_repo_id,
                anomaly_repo_id=anomaly_repo_id,
            )
        )

    LOGGER.info(
        "Threshold calibration (EER): AUROC=%.4f EER=%.4f threshold=%.4f "
        "legacy_sigma_threshold=%.4f",
        auroc,
        eer,
        eer_threshold,
        sigma_threshold,
    )
    LOGGER.info(
        "At EER threshold: fpr_normal=%.3f fpr_legal=%.3f abnormal_a_detection=%.3f",
        normal_fpr,
        legal_fpr,
        anomaly_detection_rate,
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
    LOGGER.info("CSV saved: %s", path)


def plot_results(rows: list[dict], outdir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        LOGGER.warning("matplotlib not installed; skipping plot generation.")
        return

    methods = list(dict.fromkeys(str(r.get("method", "real_nvp")) for r in rows))
    fig, axes = plt.subplots(
        len(methods),
        1,
        figsize=(9, max(4, 3.2 * len(methods))),
        squeeze=False,
    )
    datasets = ("normal_test", "legal_variation", "abnormal_a")
    for ax, method in zip(axes[:, 0], methods, strict=True):
        method_rows = [r for r in rows if str(r.get("method", "real_nvp")) == method]
        groups = {
            name: [float(r["score_value"]) for r in method_rows if r["dataset"] == name]
            for name in datasets
        }
        labels = [name for name, values in groups.items() if values]
        data = [groups[name] for name in labels]
        if data:
            ax.boxplot(data, tick_labels=labels, showfliers=False)
        score_name = str(method_rows[0].get("score_name", "score")) if method_rows else "score"
        ax.set_ylabel(score_name)
        ax.set_title(f"{method} by dataset")
        ax.grid(True, alpha=0.3)
    axes[-1, 0].set_xlabel("Dataset")
    fig.suptitle("RQ1 — L0 OOD score distributions", fontweight="bold")
    fig.tight_layout()
    out = outdir / "l0_calibration.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    LOGGER.info("Plot saved: %s", out)


def plot_roc(summary: dict, outdir: Path) -> None:
    """Plot ROC curve with EER point marked."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fpr = summary.get("roc_fpr", [])
    tpr = summary.get("roc_tpr", [])
    if not fpr or not tpr:
        return

    fig, ax = plt.subplots(1, 1, figsize=(6, 6))
    ax.plot(fpr, tpr, "b-", linewidth=2, label=f"ROC (AUROC={summary['auroc']:.4f})")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3, label="Random")

    eer_val = summary.get("eer", 0)
    ax.plot(eer_val, 1 - eer_val, "ro", markersize=10, label=f"EER={eer_val:.4f}")

    ax.set_xlabel("False Positive Rate (FPR)")
    ax.set_ylabel("True Positive Rate (TPR)")
    ax.set_title("RQ1 — L0 Real-NVP ROC Curve (Normal vs Abnormal)")
    ax.legend(loc="lower right")
    ax.set_xlim([-0.02, 1.02])
    ax.set_ylim([-0.02, 1.02])
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal")
    fig.tight_layout()
    out = outdir / "l0_roc_curve.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    LOGGER.info("ROC plot saved: %s", out)


def main() -> None:
    configure_cli_logging()
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
        "--cache-dir",
        type=str,
        default=_DEFAULT_CACHE_DIR,
        help="Cache directory for HF observations and trained Real-NVP flow.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local RQ1 dataset/model caches.",
    )
    parser.add_argument(
        "--compare-ood-methods",
        action="store_true",
        help="Also score Welford and MemoryBank alongside the default Real-NVP NLL.",
    )
    parser.add_argument("--max-observations-per-dataset", type=int, default=None)
    parser.add_argument(
        "--vision-model",
        type=str,
        default=None,
        help="HuggingFace vision model for image feature extraction (e.g. mobilenet_v3_large).",
    )
    parser.add_argument(
        "--vision-weight",
        type=float,
        default=0.3,
        help="Weight of vision features in fused embedding (0.0-1.0).",
    )
    parser.add_argument(
        "--vision-camera",
        type=str,
        default="top",
        help="Camera name to use for vision (top or wrist).",
    )
    parser.add_argument(
        "--vision-subsample",
        type=int,
        default=30,
        help="Load every Nth video frame (30 = 1 per second at 30fps).",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default="data/experiments/l0_calibration")
    parser.add_argument(
        "--runtime-model-path",
        type=str,
        default=_DEFAULT_RUNTIME_MODEL_PATH,
        help="Publish the calibrated extractor/flow for Stackfiles at this .pt path.",
    )
    parser.add_argument(
        "--no-runtime-export",
        action="store_true",
        help="Do not publish a Stackfile-compatible OOD model bundle.",
    )
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
        cache_dir=None if args.no_cache else args.cache_dir,
        vision_model=args.vision_model,
        vision_weight=args.vision_weight,
        vision_camera=args.vision_camera,
        vision_subsample=args.vision_subsample,
        runtime_model_path=None if args.no_runtime_export else args.runtime_model_path,
    )
    write_csv(rows, outdir / "results.csv")
    plot_results(rows, outdir)
    plot_roc(summary, outdir)
    with open(outdir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    LOGGER.info("Summary saved: %s", outdir / "summary.json")


if __name__ == "__main__":
    main()
