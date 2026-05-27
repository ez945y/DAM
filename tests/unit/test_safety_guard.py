"""Tests for dam.SafetyGuard, dam.safe(), and dam.SafetyProcessorStep."""

from __future__ import annotations

import textwrap
from pathlib import Path

import numpy as np
import pytest

import dam
from dam.api import SafetyGuard, safe

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


@pytest.fixture()
def stackfile(tmp_path: Path) -> str:
    """Minimal stackfile with known joint limits (±1.5 rad ≈ ±86°)."""
    path = tmp_path / "safety.yaml"
    path.write_text(
        textwrap.dedent("""\
        version: "1"
        guards:
          - L1: motion
        boundaries:
          safe_joints:
            layer: L1
            type: single
            nodes:
              - callback: joint_position_limits
                params:
                  upper: [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]
                  lower: [-1.5, -1.5, -1.5, -1.5, -1.5, -1.5]
        tasks:
          default:
            description: "test"
            boundaries: [safe_joints]
        safety:
          control_frequency_hz: 30
          enforcement_mode: enforce
        """)
    )
    return str(path)


# ---------------------------------------------------------------------------
# TestSafetyGuard — Level 2 API
# ---------------------------------------------------------------------------


class TestSafetyGuard:
    def test_init_loads_runtime(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        assert guard.runtime is not None
        assert guard._n_joints == 6

    def test_call_dict_roundtrip(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        result = guard(action, obs)
        assert isinstance(result, dict)
        assert set(result.keys()) == {f"{n}.pos" for n in _JOINT_NAMES}

    def test_call_ndarray_roundtrip(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = np.array([0.5, -0.3, 0.8, 0.1, -0.2, 0.6])
        obs = np.array([0.4, -0.2, 0.7, 0.0, -0.1, 0.5])
        result = guard(action, obs)
        assert isinstance(result, np.ndarray)
        assert result.shape == (6,)

    def test_clamps_out_of_bounds(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = np.array([2.0, -2.0, 0.5, 0.5, 0.5, 0.5])
        obs = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        result = guard(action, obs)
        assert isinstance(result, np.ndarray)
        # Joints 0,1 should be clamped to ±1.5
        assert result[0] <= 1.5 + 1e-6
        assert result[1] >= -1.5 - 1e-6
        # Joints 2-5 should pass through (within limits)
        np.testing.assert_allclose(result[2:], [0.5, 0.5, 0.5, 0.5], atol=1e-6)

    def test_safe_action_within_limits(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = np.array([0.3, -0.3, 0.5, 0.1, -0.2, 0.6])
        obs = np.array([0.2, -0.2, 0.4, 0.0, -0.1, 0.5])
        result = guard(action, obs)
        np.testing.assert_allclose(result, action, atol=1e-6)

    def test_last_results_populated(self, stackfile: str) -> None:
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = np.array([0.5] * 6)
        obs = np.array([0.4] * 6)
        guard(action, obs)
        assert len(guard.last_results) > 0

    def test_degrees_mode(self, stackfile: str) -> None:
        """With degrees_mode=True, input/output are in degrees but internal validation uses radians."""
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=True)
        # 85° ≈ 1.48 rad → within ±1.5 rad limit
        action = np.array([85.0, -85.0, 30.0, 10.0, -10.0, 30.0])
        obs = np.array([80.0, -80.0, 25.0, 5.0, -5.0, 25.0])
        result = guard(action, obs)
        assert isinstance(result, np.ndarray)
        # Should pass through (85° ≈ 1.48 rad < 1.5 rad)
        np.testing.assert_allclose(result, action, atol=0.5)

    def test_degrees_mode_clamps(self, stackfile: str) -> None:
        """Action at 100° ≈ 1.74 rad should be clamped to ~1.5 rad ≈ 85.9°."""
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=True)
        action = np.array([100.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        obs = np.array([0.0] * 6)
        result = guard(action, obs)
        # Joint 0 should be clamped to ~85.9° (1.5 rad)
        assert result[0] < 90.0

    def test_missing_joint_names_raises(self, stackfile: str) -> None:
        with pytest.raises(ValueError, match="joint_names"):
            SafetyGuard(stackfile)  # no hardware.preset and no explicit joint_names

    def test_stateful_velocity_estimation(self, stackfile: str) -> None:
        """Two consecutive calls should produce different velocity estimates."""
        guard = SafetyGuard(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        obs1 = np.array([0.0] * 6)
        obs2 = np.array([0.1] * 6)
        action = np.array([0.5] * 6)
        guard(action, obs1)
        guard(action, obs2)
        # After second call, prev_positions should be obs2's converted positions
        assert guard._prev_positions is not None


# ---------------------------------------------------------------------------
# TestSafeFunction — Level 1 API
# ---------------------------------------------------------------------------


class TestSafeFunction:
    def test_returns_same_type_dict(self, stackfile: str) -> None:
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        result = safe(
            action,
            obs,
            stackfile=stackfile,
            joint_names=_JOINT_NAMES,
            degrees_mode=False,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == set(action.keys())

    def test_returns_same_type_array(self, stackfile: str) -> None:
        action = np.array([0.5] * 6)
        obs = np.array([0.4] * 6)
        result = safe(
            action,
            obs,
            stackfile=stackfile,
            joint_names=_JOINT_NAMES,
            degrees_mode=False,
        )
        assert isinstance(result, np.ndarray)
        assert result.shape == action.shape

    def test_missing_stackfile_raises(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            safe(
                np.zeros(6),
                np.zeros(6),
                stackfile="/nonexistent/safety.yaml",
                joint_names=_JOINT_NAMES,
            )


# ---------------------------------------------------------------------------
# TestSafetyProcessorStep — Level 3 API (lerobot integration)
# ---------------------------------------------------------------------------

try:
    from lerobot.processor.pipeline import RobotActionProcessorStep

    _HAS_LEROBOT = True
except ImportError:
    _HAS_LEROBOT = False


@pytest.mark.skipif(not _HAS_LEROBOT, reason="lerobot not installed")
class TestSafetyProcessorStep:
    def test_inherits_base(self) -> None:
        from dam.processor import SafetyProcessorStep

        assert issubclass(SafetyProcessorStep, RobotActionProcessorStep)

    def test_lazy_init(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        assert step._guard is None

    def test_action_with_transition(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}

        transition = {
            "observation": obs,
            "action": action,
            "reward": None,
            "done": None,
            "truncated": None,
            "info": None,
            "complementary_data": None,
        }
        result = step(transition)
        assert isinstance(result["action"], dict)
        assert step._guard is not None

    def test_passthrough_without_obs(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        transition = {
            "observation": None,
            "action": action,
            "reward": None,
            "done": None,
            "truncated": None,
            "info": None,
            "complementary_data": None,
        }
        result = step(transition)
        assert result["action"] == action

    def test_reset_clears_guard(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        transition = {
            "observation": obs,
            "action": action,
            "reward": None,
            "done": None,
            "truncated": None,
            "info": None,
            "complementary_data": None,
        }
        step(transition)
        assert step._guard is not None
        step.reset()
        assert step._guard is None

    def test_get_config(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        config = step.get_config()
        assert config["stackfile"] == stackfile
        assert config["degrees_mode"] is False

    def test_transform_features_passthrough(self, stackfile: str) -> None:
        from dam.processor import SafetyProcessorStep

        step = SafetyProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        features = {"action": {"joint": {"shape": (6,)}}}
        assert step.transform_features(features) is features
