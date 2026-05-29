"""
Safety regression: human-collaborative motion boundaries.

Known dangerous scenarios for the L1 collaborative callbacks MUST be
rejected. These tests NEVER allow an unsafe condition to pass through.

Covers:
  1. The new callbacks are registered by register_all() and callable.
  2. End-effector entering a configured keep-out volume is rejected.
  3. End-effector tilt past the payload-spill limit is rejected.
  4. Mobile base leaving geofence is rejected.
"""

from __future__ import annotations

import numpy as np

from dam.boundary.builtin_callbacks import (
    base_geofence,
    keep_out_zone,
    orientation_limit,
    register_all,
)
from dam.registry.callback import CallbackRegistry
from dam.types.observation import Observation


def _obs(positions=None, velocities=None, ee_pose=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.array(positions) if positions is not None else np.zeros(6),
        joint_velocities=np.array(velocities) if velocities is not None else None,
        end_effector_pose=np.array(ee_pose) if ee_pose is not None else None,
    )


# ── Registration ──────────────────────────────────────────────────────────────


class TestCollaborativeCallbacksRegistered:
    def test_registered_by_register_all(self):
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            registered = rcmod._registry.list_all()
            assert "keep_out_zone" in registered
            assert "orientation_limit" in registered
            assert "base_geofence" in registered
        finally:
            rcmod._registry = orig


# ── Dangerous scenarios MUST be rejected ──────────────────────────────────────


class TestDangerousScenariosRejected:
    def test_enters_keep_out_volume_rejected(self):
        """EE inside an operator keep-out box must be rejected."""
        obs = _obs(ee_pose=[0.0, 0.0, 0.2, 0, 0, 0, 1])
        boxes = [[[-0.2, 0.2], [-0.2, 0.2], [0.0, 0.4]]]
        result = keep_out_zone(obs=obs, boxes=boxes)
        assert result is not True
        assert result[0] is False

    def test_payload_tipped_rejected(self):
        """Carrying axis tilted 90° from vertical must be rejected."""
        s = float(np.sin(np.pi / 4))
        obs = _obs(ee_pose=[0, 0, 0.3, s, 0, 0, s])  # 90° about x
        result = orientation_limit(obs=obs, max_tilt_deg=20.0)
        assert result is not True
        assert result[0] is False

    def test_base_leaves_geofence_rejected(self):
        """Mobile base driving outside its geofence must be rejected."""
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.zeros(6),
            channels={"base_pose": np.array([3.0, 0.0, 0.0])},
        )
        result = base_geofence(obs=obs, bounds=[[-1.0, 1.0], [-1.0, 1.0]])
        assert result is not True
        assert result[0] is False
