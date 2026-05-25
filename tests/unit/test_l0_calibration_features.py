from __future__ import annotations

import numpy as np

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
