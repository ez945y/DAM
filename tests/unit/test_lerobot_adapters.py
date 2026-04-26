"""Unit tests for LeRobot adapters using mock robot/policy objects.

Covers:
  - Source adapter: legacy capture_observation() and modern get_observation() APIs
  - Source adapter: degrees → radians conversion (and gripper exempt)
  - Sink adapter: named-joint dict format + radians → degrees conversion
  - Sink adapter: gripper exempt from degree conversion
  - Policy adapter: select_action() path + chunk flattening
"""

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
from dam.adapter.lerobot.sink import LeRobotSinkAdapter
from dam.adapter.lerobot.source import LeRobotSourceAdapter
from dam.types.action import ValidatedAction
from dam.types.observation import Observation

try:
    import lerobot  # noqa: F401

    HAS_LEROBOT = True
except ImportError:
    HAS_LEROBOT = False
    lerobot = None

requires_lerobot = pytest.mark.skipif(not HAS_LEROBOT, reason="lerobot not installed")

# ── Mock robots ───────────────────────────────────────────────────────────────


class LegacyMockRobot:
    """Simulates old lerobot ≤0.2 capture_observation() API."""

    def __init__(self, joint_positions=None):
        self._positions = joint_positions or [0.1, 0.2, 0.3, 0.1, 0.0, 0.5]
        self.last_action = None

    def capture_observation(self):
        return {"observation.state": self._positions}

    def send_action(self, action_dict):
        self.last_action = action_dict


class ModernMockRobot:
    """Simulates modern lerobot ≥0.2 get_observation() API returning degrees."""

    def __init__(self, positions_deg=None):
        # Default: all joints at 90 degrees, gripper at 0.0 (normalised)
        self._pos_deg = positions_deg or {
            "shoulder_pan": 90.0,
            "shoulder_lift": 45.0,
            "elbow_flex": 30.0,
            "wrist_flex": -45.0,
            "wrist_roll": 0.0,
            "gripper": 0.02,  # gripper in metres (not degrees)
        }
        self.last_action = None

    def get_observation(self):
        return {f"{k}.pos": v for k, v in self._pos_deg.items()}

    def send_action(self, action_dict):
        self.last_action = action_dict


class MockPolicy:
    def __init__(self, action=None):
        self._action = action or [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]

    def select_action(self, obs_dict):
        return np.array(self._action)


# ── Source adapter — legacy API ───────────────────────────────────────────────


def test_source_legacy_read_converts_to_observation():
    robot = LegacyMockRobot([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    adapter = LeRobotSourceAdapter(robot)
    obs = adapter.read()
    assert isinstance(obs, Observation)
    assert len(obs.joint_positions) == 6
    np.testing.assert_allclose(obs.joint_positions, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])


def test_source_velocity_estimated_on_first_read():
    robot = LegacyMockRobot()
    adapter = LeRobotSourceAdapter(robot)
    obs = adapter.read()
    assert obs is not None
    assert obs.joint_velocities.shape == (6,)
    # First read: velocities should be zero (no previous)
    np.testing.assert_allclose(obs.joint_velocities, np.zeros(6))


# ── Source adapter — modern API ───────────────────────────────────────────────


def test_source_modern_api_dispatched_correctly():
    """get_observation() dict with .pos keys should be dispatched to _convert_named."""
    robot = ModernMockRobot()
    adapter = LeRobotSourceAdapter(robot, degrees_mode=True)
    obs = adapter.read()
    assert isinstance(obs, Observation)
    assert len(obs.joint_positions) == 6


def test_source_modern_degrees_to_radians():
    """Revolute joints in degrees must be converted to radians."""
    robot = ModernMockRobot(
        {
            "shoulder_pan": 90.0,
            "shoulder_lift": 0.0,
            "elbow_flex": 0.0,
            "wrist_flex": 0.0,
            "wrist_roll": 0.0,
            "gripper": 0.0,
        }
    )
    adapter = LeRobotSourceAdapter(robot, degrees_mode=True)
    obs = adapter.read()
    assert obs is not None
    # shoulder_pan at 90° → π/2 rad
    assert abs(obs.joint_positions[0] - math.pi / 2) < 1e-9


def test_source_modern_gripper_exempt_from_degree_conversion():
    """Gripper joint must NOT be degree-converted (it's a linear/normalised value)."""
    robot = ModernMockRobot(
        {
            "shoulder_pan": 0.0,
            "shoulder_lift": 0.0,
            "elbow_flex": 0.0,
            "wrist_flex": 0.0,
            "wrist_roll": 0.0,
            "gripper": 0.035,
        }
    )
    adapter = LeRobotSourceAdapter(robot, degrees_mode=True)
    obs = adapter.read()
    assert obs is not None
    # gripper index is 5 — value must pass through as 0.035, not converted
    assert abs(obs.joint_positions[5] - 0.035) < 1e-9


def test_source_modern_radians_mode_no_conversion():
    """When degrees_mode=False, no conversion should happen."""
    robot = ModernMockRobot(
        {
            "shoulder_pan": 1.57,
            "shoulder_lift": 0.0,
            "elbow_flex": 0.0,
            "wrist_flex": 0.0,
            "wrist_roll": 0.0,
            "gripper": 0.0,
        }
    )
    adapter = LeRobotSourceAdapter(robot, degrees_mode=False)
    obs = adapter.read()
    assert obs is not None
    assert abs(obs.joint_positions[0] - 1.57) < 1e-9


# ── Sink adapter ──────────────────────────────────────────────────────────────


def test_sink_produces_named_joint_dict():
    """Sink adapter must produce named-joint dict (modern lerobot format)."""
    robot = LegacyMockRobot()
    adapter = LeRobotSinkAdapter(robot, degrees_mode=True)
    action = ValidatedAction(target_joint_positions=np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]))
    adapter.apply(action)
    assert robot.last_action is not None
    # Modern format: named-joint keys like "shoulder_pan.pos"
    assert "shoulder_pan.pos" in robot.last_action
    assert "shoulder_lift.pos" in robot.last_action
    # Gripper key must be present
    assert "gripper.pos" in robot.last_action


def test_sink_radians_to_degrees_conversion():
    """Revolute joints in radians must be converted to degrees for the robot."""
    robot = LegacyMockRobot()
    adapter = LeRobotSinkAdapter(robot, degrees_mode=True)
    action = ValidatedAction(target_joint_positions=np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]))
    adapter.apply(action)
    # shoulder_pan index 0: 0.1 rad → math.degrees(0.1)
    assert abs(robot.last_action["shoulder_pan.pos"] - math.degrees(0.1)) < 1e-9


def test_sink_gripper_exempt_from_degree_conversion():
    """Gripper joint must NOT be converted (it's a linear/normalised value)."""
    robot = LegacyMockRobot()
    adapter = LeRobotSinkAdapter(robot, degrees_mode=True)
    gripper_val = 0.035
    positions = np.array([0.0, 0.0, 0.0, 0.0, 0.0, gripper_val])
    action = ValidatedAction(target_joint_positions=positions)
    adapter.apply(action)
    # Gripper must pass through unchanged
    assert abs(robot.last_action["gripper.pos"] - gripper_val) < 1e-9


def test_sink_write_alias_calls_apply():
    """write() is a deprecated alias for apply() and must behave identically."""
    robot = LegacyMockRobot()
    adapter = LeRobotSinkAdapter(robot, degrees_mode=False)
    action = ValidatedAction(target_joint_positions=np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
    adapter.write(action)
    assert robot.last_action is not None
    assert "shoulder_pan.pos" in robot.last_action


def test_sink_no_degree_conversion_when_disabled():
    """When degrees_mode=False, values must pass through unchanged."""
    robot = LegacyMockRobot()
    adapter = LeRobotSinkAdapter(robot, degrees_mode=False)
    val = 1.234
    action = ValidatedAction(target_joint_positions=np.array([val, 0, 0, 0, 0, 0]))
    adapter.apply(action)
    assert abs(robot.last_action["shoulder_pan.pos"] - val) < 1e-9


# ── Policy adapter ────────────────────────────────────────────────────────────


@requires_lerobot
def test_policy_predict_returns_action_proposal():
    mock_policy = MagicMock()
    mock_pre = MagicMock()
    mock_post = MagicMock()
    adapter = LeRobotPolicyAdapter(mock_policy, preprocessor=mock_pre, postprocessor=mock_post)

    obs = Observation(
        timestamp=123.456,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
    )

    # Mock the official predict_action helper
    with patch("lerobot.utils.control_utils.predict_action") as mock_predict:
        # Simulate Diffusion multi-step return [T, D]
        mock_predict.return_value = np.zeros((10, 7))  # 10 steps, 7 values (6 joints + 1 gripper)
        mock_predict.return_value[0, 5] = 0.5  # joints[5] = 0.5
        mock_predict.return_value[0, 6] = 0.042  # gripper = 0.042

        proposal = adapter.predict(obs)

    assert abs(proposal.timestamp - 123.456) < 1e-9
    assert len(proposal.target_joint_positions) == 6
    assert np.allclose(proposal.target_joint_positions[5], np.radians(0.5))
    assert abs(proposal.gripper_action - 0.042) < 1e-9
