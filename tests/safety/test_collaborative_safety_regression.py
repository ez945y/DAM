"""
Safety regression: human-collaborative motion boundaries.

Known dangerous scenarios for the L1 collaborative callbacks MUST be
rejected. These tests NEVER allow an unsafe condition to pass through.

Covers:
  1. The new callbacks are registered by register_all() and callable.
  2. Workspace violation halts motion.
"""

from __future__ import annotations

import numpy as np

from dam.boundary.builtin_callbacks import (
    register_all,
    workspace,
)
from dam.registry.callback import CallbackRegistry
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


def _obs(positions=None, ee_pose=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.array(positions) if positions is not None else np.zeros(6),
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
            assert "workspace" in registered
        finally:
            rcmod._registry = orig


# ── Dangerous scenarios MUST be rejected ──────────────────────────────────────


class TestDangerousScenariosRejected:
    def test_workspace_breach_halts(self):
        """EE outside workspace must be clamped to halt."""
        obs = _obs(ee_pose=[0.5, 0.1, 0.3, 0, 0, 0, 1])
        action = ActionProposal(target_joint_positions=np.zeros(6))
        bounds = [[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]
        result = workspace(obs=obs, action=action, bounds=bounds)
        assert result.decision == GuardDecision.CLAMP
        np.testing.assert_array_equal(
            result.clamped_action.target_joint_positions, obs.joint_positions
        )
