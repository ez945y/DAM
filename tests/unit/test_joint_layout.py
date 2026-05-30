"""Tests for JointLayout contract."""

import numpy as np
import pytest

from dam.types.joint_layout import JointLayout


class TestJointLayout:
    def test_from_dict_basic(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2, 3, 4], "gripper": [5]})
        assert layout.n_joints == 6
        assert layout.has("arm")
        assert layout.has("gripper")
        assert not layout.has("base")

    def test_indices(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2], "gripper": [3, 4]})
        np.testing.assert_array_equal(layout.indices("arm"), [0, 1, 2])
        np.testing.assert_array_equal(layout.indices("gripper"), [3, 4])
        np.testing.assert_array_equal(layout.indices("arm", "gripper"), [0, 1, 2, 3, 4])

    def test_mask(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2], "gripper": [3]})
        np.testing.assert_array_equal(layout.mask("arm"), [True, True, True, False])
        np.testing.assert_array_equal(layout.mask("gripper"), [False, False, False, True])

    def test_slice_for_contiguous(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2, 3, 4], "gripper": [5]})
        assert layout.slice_for("arm") == slice(0, 5)
        assert layout.slice_for("gripper") == slice(5, 6)

    def test_slice_for_non_contiguous(self):
        layout = JointLayout.from_dict({"selected": [0, 2, 4]})
        assert layout.slice_for("selected") is None

    def test_group_of(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2], "gripper": [3]})
        assert layout.group_of(0) == "arm"
        assert layout.group_of(3) == "gripper"
        assert layout.group_of(99) is None

    def test_from_names_so101(self):
        names = [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
            "gripper",
        ]
        layout = JointLayout.from_names(names)
        assert layout.groups["arm"] == [0, 1, 2, 3, 4]
        assert layout.groups["gripper"] == [5]
        assert layout.names == names

    def test_from_names_no_gripper(self):
        names = ["j1", "j2", "j3"]
        layout = JointLayout.from_names(names)
        assert layout.groups["arm"] == [0, 1, 2]
        assert "gripper" not in layout.groups

    def test_from_names_dual_gripper(self):
        names = ["j1", "j2", "j3", "left_finger", "right_finger"]
        layout = JointLayout.from_names(names)
        assert layout.groups["arm"] == [0, 1, 2]
        assert layout.groups["gripper"] == [3, 4]

    def test_trivial(self):
        layout = JointLayout.trivial(6)
        assert layout.groups["arm"] == [0, 1, 2, 3, 4, 5]
        assert layout.n_joints == 6

    def test_amr_layout(self):
        layout = JointLayout.from_dict(
            {
                "base": [0, 1],
                "arm": [2, 3, 4, 5, 6, 7],
                "gripper": [8],
            }
        )
        assert layout.n_joints == 9
        np.testing.assert_array_equal(layout.indices("arm"), [2, 3, 4, 5, 6, 7])
        assert layout.slice_for("base") == slice(0, 2)

    def test_humanoid_layout(self):
        layout = JointLayout.from_dict(
            {
                "torso": [0, 1, 2],
                "left_arm": [3, 4, 5, 6, 7, 8],
                "right_arm": [9, 10, 11, 12, 13, 14],
                "left_gripper": [15],
                "right_gripper": [16],
            }
        )
        assert layout.n_joints == 17
        np.testing.assert_array_equal(
            layout.indices("left_arm", "right_arm"),
            [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        )
        assert layout.group_of(15) == "left_gripper"

    def test_empty_group_returns_empty(self):
        layout = JointLayout.from_dict({"arm": [0, 1, 2]})
        np.testing.assert_array_equal(layout.indices("nonexistent"), [])
        np.testing.assert_array_equal(layout.mask("nonexistent"), [False, False, False])

    def test_names_property(self):
        layout = JointLayout.from_dict({"arm": [0, 1]}, names=["shoulder", "elbow"])
        assert layout.names == ["shoulder", "elbow"]
        assert layout.n_joints == 2
