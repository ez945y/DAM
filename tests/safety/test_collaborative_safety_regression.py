"""
Safety regression: human-collaborative motion boundaries.

Known dangerous scenarios for the L1 collaborative callbacks MUST be
rejected. These tests NEVER allow an unsafe condition to pass through.

Covers:
  1. The new callbacks are registered by register_all() and callable.
  2. End-effector speed above the ISO/TS 15066 reduced-speed cap is rejected.
  3. End-effector entering a configured keep-out volume is rejected.
  4. End-effector tilt past the payload-spill limit is rejected.
"""

from __future__ import annotations

import numpy as np

from dam.boundary.builtin_callbacks import (
    cartesian_velocity_limit,
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


class _Placement:
    def __init__(self, translation, rotation):
        self.translation = np.asarray(translation, dtype=float)
        self.rotation = np.asarray(rotation, dtype=float)


class _Dynamics:
    def __init__(self, jac=None, translation=None, rotation=None):
        self._jac = jac
        self._placement = _Placement(
            translation if translation is not None else [0.0, 0.0, 0.0],
            rotation if rotation is not None else np.eye(3),
        )
        self.available = True
        self.default_frame_id = 0

    def update(self, q):  # noqa: D401 - test stub
        pass

    def frame_jacobian(self, _fid):
        return self._jac

    def frame_placement(self, _fid):
        return self._placement


# ── Registration ──────────────────────────────────────────────────────────────


class TestCollaborativeCallbacksRegistered:
    def test_registered_by_register_all(self):
        import dam.registry.callback as rcmod

        orig = rcmod._registry
        rcmod._registry = CallbackRegistry()
        try:
            register_all()
            registered = rcmod._registry.list_all()
            assert "cartesian_velocity_limit" in registered
            assert "keep_out_zone" in registered
            assert "orientation_limit" in registered
        finally:
            rcmod._registry = orig


# ── Dangerous scenarios MUST be rejected ──────────────────────────────────────


class TestDangerousScenariosRejected:
    def test_excessive_ee_speed_rejected(self):
        """EE driven at 1 m/s with a 0.25 m/s cap must be rejected."""
        obs = _obs(positions=[0.0] * 6, velocities=[1.0, 0, 0, 0, 0, 0])
        dyn = _Dynamics(jac=np.eye(6))
        result = cartesian_velocity_limit(obs=obs, dynamics=dyn, max_linear_speed=0.25)
        assert result is not True
        assert result[0] is False

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
