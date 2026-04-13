"""Unit tests for hardware presets.

Covers:
  - get_preset() for all known robot models
  - Joint name ordering matches spec (shoulder_pan first, gripper last)
  - All limit values are in radians (not degrees)
  - is_gripper detection for each preset
  - list_presets() returns all expected names
  - Unknown preset raises KeyError with helpful message
  - RobotPreset property accessors
"""

import math

import pytest

from dam.adapter.lerobot.presets import (
    GENERIC_6DOF,
    KOCH_FOLLOWER,
    SO100_FOLLOWER,
    SO101_FOLLOWER,
    get_preset,
    list_presets,
)

# ── list_presets ──────────────────────────────────────────────────────────────


def test_list_presets_contains_known_robots():
    names = list_presets()
    assert "so101_follower" in names
    assert "so100_follower" in names
    assert "koch_follower" in names
    assert "generic_6dof" in names


def test_list_presets_is_sorted():
    names = list_presets()
    assert names == sorted(names)


# ── get_preset ────────────────────────────────────────────────────────────────


def test_get_preset_so101_by_name():
    p = get_preset("so101_follower")
    assert p.name == "so101_follower"


def test_get_preset_case_insensitive_via_hyphen():
    """Hyphens in name should be replaced with underscores."""
    p = get_preset("so101-follower")
    assert p.name == "so101_follower"


def test_get_preset_unknown_raises_key_error():
    with pytest.raises(KeyError, match="Unknown robot preset"):
        get_preset("nonexistent_robot")


# ── SO101 preset ──────────────────────────────────────────────────────────────


class TestSO101Preset:
    def test_joint_count(self):
        assert len(SO101_FOLLOWER.joints) == 6

    def test_joint_names_order(self):
        names = SO101_FOLLOWER.joint_names
        assert names[0] == "shoulder_pan"
        assert names[-1] == "gripper"

    def test_degrees_mode_is_true(self):
        assert SO101_FOLLOWER.degrees_mode is True

    def test_limits_are_in_radians(self):
        """shoulder_pan limits must be ~±110° expressed in radians."""
        joint = SO101_FOLLOWER.joints[0]  # shoulder_pan
        assert abs(joint.lower_rad - math.radians(-110)) < 1e-9
        assert abs(joint.upper_rad - math.radians(110)) < 1e-9

    def test_gripper_joint_is_flagged(self):
        gripper = SO101_FOLLOWER.joints[-1]
        assert gripper.is_gripper is True

    def test_revolute_joints_not_flagged_as_gripper(self):
        for joint in SO101_FOLLOWER.joints[:-1]:
            assert joint.is_gripper is False

    def test_upper_limits_property(self):
        ul = SO101_FOLLOWER.upper_limits
        assert len(ul) == 6
        # All revolute joints have positive upper limits
        for u in ul[:-1]:
            assert u > 0

    def test_lower_limits_property(self):
        ll = SO101_FOLLOWER.lower_limits
        assert len(ll) == 6
        # All revolute joints have negative lower limits
        for limit in ll[:-1]:
            assert limit < 0

    def test_max_velocities_property(self):
        mv = SO101_FOLLOWER.max_velocities
        assert len(mv) == 6
        assert all(v > 0 for v in mv)


# ── SO100 preset ──────────────────────────────────────────────────────────────


class TestSO100Preset:
    def test_same_motions_as_so101(self):
        """SO100 and SO101 have the same joint config (different HW revision)."""
        for s0, s1 in zip(SO100_FOLLOWER.joints, SO101_FOLLOWER.joints, strict=False):
            assert s0.name == s1.name
            assert abs(s0.lower_rad - s1.lower_rad) < 1e-9
            assert abs(s0.upper_rad - s1.upper_rad) < 1e-9

    def test_degrees_mode_is_true(self):
        assert SO100_FOLLOWER.degrees_mode is True


# ── Koch preset ───────────────────────────────────────────────────────────────


class TestKochPreset:
    def test_joint_count(self):
        assert len(KOCH_FOLLOWER.joints) == 6

    def test_degrees_mode_is_true(self):
        assert KOCH_FOLLOWER.degrees_mode is True

    def test_revolute_limits_are_full_rotation(self):
        """Koch joints allow ±180°."""
        for joint in KOCH_FOLLOWER.joints[:-1]:
            assert abs(joint.upper_rad - math.pi) < 1e-9


# ── Generic 6DOF preset ───────────────────────────────────────────────────────


class TestGeneric6DOFPreset:
    def test_joint_count(self):
        assert len(GENERIC_6DOF.joints) == 6

    def test_degrees_mode_is_false(self):
        """Generic preset speaks radians natively."""
        assert GENERIC_6DOF.degrees_mode is False

    def test_limits_are_pi(self):
        for joint in GENERIC_6DOF.joints:
            assert abs(joint.upper_rad - math.pi) < 1e-9
            assert abs(joint.lower_rad + math.pi) < 1e-9

    def test_joint_names_sequential(self):
        names = GENERIC_6DOF.joint_names
        for i, name in enumerate(names):
            assert name == f"joint_{i}"


# ── RobotPreset properties ────────────────────────────────────────────────────


def test_preset_control_hz_default():
    assert SO101_FOLLOWER.control_hz == 50.0


def test_preset_joint_names_len_matches_joints():
    for name in list_presets():
        p = get_preset(name)
        assert len(p.joint_names) == len(p.joints)
        assert len(p.upper_limits) == len(p.joints)
        assert len(p.lower_limits) == len(p.joints)
        assert len(p.max_velocities) == len(p.joints)
