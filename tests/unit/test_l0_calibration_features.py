from __future__ import annotations

import json

import numpy as np
import pytest

from dam.types.observation import Observation
from scripts import run_l0_calibration as cal


def _obs(joint_positions: list[float], timestamp: float) -> Observation:
    return Observation(
        timestamp=timestamp,
        joint_positions=np.asarray(joint_positions, dtype=np.float64),
    )


def _labelled_episode(
    episode_index: int,
    positions: list[list[float]],
    *,
    dt: float = 0.1,
) -> list[tuple[int, int, Observation]]:
    return [
        (episode_index, frame_index, _obs(frame_positions, frame_index * dt))
        for frame_index, frame_positions in enumerate(positions)
    ]


def test_temporal_sequence_scores_rank_jumpy_abnormal_above_smooth_normal() -> None:
    smooth_normal = _labelled_episode(
        0,
        [
            [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
            [0.01, 0.01, 0.00, 0.00, 0.00, 0.00],
            [0.02, 0.02, 0.00, 0.00, 0.00, 0.00],
            [0.03, 0.03, 0.00, 0.00, 0.00, 0.00],
        ],
    )
    jumpy_abnormal = _labelled_episode(
        1,
        [
            [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
            [0.01, 0.01, 0.00, 0.00, 0.00, 0.00],
            [1.25, -1.10, 0.95, 0.00, 0.00, 0.00],
            [1.26, -1.09, 0.96, 0.00, 0.00, 0.00],
        ],
    )

    normal_scores = cal._temporal_sequence_scores(smooth_normal)
    abnormal_scores = cal._temporal_sequence_scores(jumpy_abnormal)

    assert normal_scores.shape == (len(smooth_normal),)
    assert abnormal_scores.shape == (len(jumpy_abnormal),)
    assert np.all(np.isfinite(normal_scores))
    assert np.all(np.isfinite(abnormal_scores))
    assert abnormal_scores[2] > normal_scores.max() * 20
    assert abnormal_scores[2] == abnormal_scores.max()


def test_temporal_sequence_scores_reset_at_episode_boundaries() -> None:
    labelled = _labelled_episode(
        0,
        [
            [0.00, 0.00, 0.00, 0.00, 0.00, 0.00],
            [0.01, 0.01, 0.00, 0.00, 0.00, 0.00],
        ],
    ) + _labelled_episode(
        1,
        [
            [2.00, -2.00, 1.50, 0.00, 0.00, 0.00],
            [2.01, -1.99, 1.50, 0.00, 0.00, 0.00],
        ],
    )

    scores = cal._temporal_sequence_scores(labelled)

    assert scores.shape == (len(labelled),)
    assert scores[0] == 0.0
    assert scores[2] == 0.0
    assert scores[1] > 0.0
    assert scores[3] > 0.0


def test_compute_roc_and_eer_perfect_separation() -> None:
    """Well-separated distributions should yield AUROC ≈ 1 and EER ≈ 0."""
    rng = np.random.default_rng(0)
    normal = rng.normal(-500, 5, 200)
    anomaly = rng.normal(-400, 5, 200)

    result = cal._compute_roc_and_eer(normal, anomaly)

    assert result["auroc"] > 0.99
    assert result["eer"] < 0.02
    assert -510 < result["eer_threshold"] < -390


def test_compute_roc_and_eer_overlapping_distributions() -> None:
    """Highly overlapping distributions should yield AUROC ≈ 0.5."""
    rng = np.random.default_rng(1)
    normal = rng.normal(0, 1, 500)
    anomaly = rng.normal(0, 1, 500)

    result = cal._compute_roc_and_eer(normal, anomaly)

    assert 0.4 < result["auroc"] < 0.6
    assert 0.35 < result["eer"] < 0.65


def test_flow_cache_key_includes_feature_configuration(tmp_path) -> None:
    vectors = np.zeros((3, 128), dtype=np.float32)
    state_only = cal._flow_cache_path(
        tmp_path,
        normal_repo_id="normal",
        max_observations_per_dataset=3,
        flow_epochs=1,
        train_vectors=vectors,
        feature_config={"state": "observation.state", "vision_model": None},
    )
    with_vision = cal._flow_cache_path(
        tmp_path,
        normal_repo_id="normal",
        max_observations_per_dataset=3,
        flow_epochs=1,
        train_vectors=vectors,
        feature_config={"state": "observation.state", "vision_model": "mobilenet_v3_small"},
    )

    assert state_only != with_vision


def test_feature_cache_round_trip_preserves_scored_rows_and_vision_metadata(tmp_path) -> None:
    path = tmp_path / "features.npz"
    train_vectors = np.ones((2, 4), dtype=np.float32)
    eval_vectors = {
        "normal_test": np.ones((1, 4), dtype=np.float32),
        "legal_variation": np.full((1, 4), 2, dtype=np.float32),
        "abnormal_a": np.full((1, 4), 3, dtype=np.float32),
    }
    kept_indices = {
        "train": np.array([0, 30]),
        "normal_test": np.array([0]),
        "legal_variation": np.array([30]),
        "abnormal_a": np.array([60]),
    }
    frames = {"train": 2, "normal_test": 1, "legal_variation": 1, "abnormal_a": 1}
    candidates = {"train": 60, "normal_test": 30, "legal_variation": 60, "abnormal_a": 90}

    cal._save_feature_cache(
        path,
        train_vectors=train_vectors,
        eval_vectors=eval_vectors,
        kept_indices=kept_indices,
        vision_frames=frames,
        vision_candidates=candidates,
    )
    loaded_train, loaded_eval, loaded_indices, loaded_frames, loaded_candidates = (
        cal._load_feature_cache(path)
    )

    np.testing.assert_array_equal(loaded_train, train_vectors)
    np.testing.assert_array_equal(loaded_eval["abnormal_a"], eval_vectors["abnormal_a"])
    np.testing.assert_array_equal(
        loaded_indices["legal_variation"], kept_indices["legal_variation"]
    )
    assert loaded_frames == frames
    assert loaded_candidates == candidates


def test_runtime_export_only_reuses_feature_cache_with_extractor_sidecar(tmp_path) -> None:
    feature_path = tmp_path / "features.npz"
    feature_path.write_bytes(b"cached-vectors")

    assert cal._feature_cache_ready_for_run(feature_path, None)
    assert not cal._feature_cache_ready_for_run(feature_path, "data/ood_models/ood_model.pt")

    cal._feature_extractor_cache_path(feature_path).write_bytes(b"cached-extractor")
    assert cal._feature_cache_ready_for_run(feature_path, "data/ood_models/ood_model.pt")


def test_comparison_memory_bank_accepts_vision_fused_vectors() -> None:
    train_vectors = np.zeros((4, 256), dtype=np.float32)
    train_vectors[:, 0] = 1.0
    eval_vectors = train_vectors[:2].copy()

    scores = cal._memory_bank_scores(train_vectors, eval_vectors)

    np.testing.assert_allclose(scores, 0.0, atol=1e-6)


def test_export_runtime_bundle_matches_stackfile_model_convention(tmp_path) -> None:
    class DummyExtractor:
        def save(self, path: str) -> None:
            from pathlib import Path

            Path(path).write_bytes(b"extractor")

    class DummyBackend:
        def save(self, path) -> None:
            path.write_bytes(b"flow")

    result = cal._export_runtime_bundle(
        backend=DummyBackend(),  # type: ignore[arg-type]
        runtime_model_path=tmp_path / "ood_model.pt",
        extractor=DummyExtractor(),
        extractor_source_path=None,
        eer_threshold=-12.34567,
        feature_config={
            "state": "observation.state",
            "vision_model": "mobilenet_v3_large",
            "vision_weight": 0.3,
            "vision_camera": "top",
        },
        normal_repo_id="normal",
        legal_repo_id="legal",
        anomaly_repo_id="abnormal",
    )

    assert (tmp_path / "ood_model.pt").read_bytes() == b"extractor"
    assert (tmp_path / "ood_model_flow.pt").read_bytes() == b"flow"
    assert result["runtime_flow_path"] == str(tmp_path / "ood_model_flow.pt")
    assert (
        json.loads((tmp_path / "ood_model.json").read_text())["stackfile_params"]
        == result["stackfile_params"]
    )
    assert result["stackfile_params"] == {
        "ood_model_path": str(tmp_path / "ood_model.pt"),
        "nll_sigma": 0,
        "nll_threshold": -12.3457,
        "vision_model": "mobilenet_v3_large",
        "vision_weight": 0.3,
        "vision_camera": "top",
    }


def test_export_runtime_bundle_does_not_reuse_stale_extractor_destination(tmp_path) -> None:
    class DummyBackend:
        def save(self, path) -> None:
            path.write_bytes(b"flow")

    model_path = tmp_path / "ood_model.pt"
    model_path.write_bytes(b"old-extractor")

    with pytest.raises(RuntimeError, match="cached feature extractor sidecar"):
        cal._export_runtime_bundle(
            backend=DummyBackend(),  # type: ignore[arg-type]
            runtime_model_path=model_path,
            extractor=None,
            extractor_source_path=tmp_path / "missing.extractor.pt",
            eer_threshold=1.0,
            feature_config={"state": "observation.state", "vision_model": None},
            normal_repo_id="normal",
            legal_repo_id="legal",
            anomaly_repo_id="abnormal",
        )

    assert model_path.read_bytes() == b"old-extractor"
    assert not (tmp_path / "ood_model_flow.pt").exists()
