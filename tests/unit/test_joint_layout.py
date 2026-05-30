"""Tests for JointLayout contract."""

import numpy as np
import pytest

from dam.types.joint_layout import JointChain, JointLayout


class TestJointChain:
    def test_all_indices(self):
        chain = JointChain(indices=[0, 1, 2], gripper=[5])
        assert chain.all_indices == [0, 1, 2, 5]

    def test_has_gripper(self):
        assert JointChain(indices=[0], gripper=[1]).has_gripper
        assert not JointChain(indices=[0]).has_gripper


class TestJointLayout:
    def test_from_config_full_form(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2, 3, 4], "gripper": [5]},
            }
        )
        assert layout.n_joints == 6
        assert layout.has("arm")
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4]
        assert layout.chains["arm"].gripper == [5]

    def test_from_config_short_form(self):
        layout = JointLayout.from_config({"torso": [0, 1, 2]})
        assert layout.chains["torso"].indices == [0, 1, 2]
        assert layout.chains["torso"].gripper == []

    def test_from_config_mixed_forms(self):
        layout = JointLayout.from_config(
            {
                "torso": [0, 1, 2],
                "arm": {"joints": [3, 4, 5], "gripper": [6]},
            }
        )
        assert layout.n_joints == 7
        assert layout.chains["torso"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == [6]

    def test_joint_indices(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        np.testing.assert_array_equal(layout.joint_indices("arm"), [0, 1, 2])

    def test_gripper_indices(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3, 4]},
            }
        )
        np.testing.assert_array_equal(layout.gripper_indices("arm"), [3, 4])

    def test_all_indices_query(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        np.testing.assert_array_equal(layout.all_indices("arm"), [0, 1, 2, 3])

    def test_multi_chain_indices(self):
        layout = JointLayout.from_config(
            {
                "left_arm": {"joints": [0, 1, 2], "gripper": [6]},
                "right_arm": {"joints": [3, 4, 5], "gripper": [7]},
            }
        )
        np.testing.assert_array_equal(
            layout.joint_indices("left_arm", "right_arm"),
            [0, 1, 2, 3, 4, 5],
        )
        np.testing.assert_array_equal(
            layout.gripper_indices("left_arm", "right_arm"),
            [6, 7],
        )

    def test_mask_with_gripper(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        np.testing.assert_array_equal(
            layout.mask("arm", include_gripper=True),
            [True, True, True, True],
        )

    def test_mask_without_gripper(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        np.testing.assert_array_equal(
            layout.mask("arm", include_gripper=False),
            [True, True, True, False],
        )

    def test_chain_of(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        assert layout.chain_of(0) == "arm"
        assert layout.chain_of(3) == "arm"  # gripper belongs to arm
        assert layout.chain_of(99) is None

    def test_is_gripper(self):
        layout = JointLayout.from_config(
            {
                "arm": {"joints": [0, 1, 2], "gripper": [3]},
            }
        )
        assert not layout.is_gripper(0)
        assert layout.is_gripper(3)

    def test_chains_with_gripper(self):
        layout = JointLayout.from_config(
            {
                "torso": [0, 1, 2],
                "left_arm": {"joints": [3, 4, 5], "gripper": [9]},
                "right_arm": {"joints": [6, 7, 8], "gripper": [10]},
            }
        )
        assert sorted(layout.chains_with_gripper()) == ["left_arm", "right_arm"]

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
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4]
        assert layout.chains["arm"].gripper == [5]
        assert layout.names == names

    def test_from_names_no_gripper(self):
        names = ["j1", "j2", "j3"]
        layout = JointLayout.from_names(names)
        assert layout.chains["arm"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == []

    def test_from_names_dual_finger(self):
        names = ["j1", "j2", "j3", "left_finger", "right_finger"]
        layout = JointLayout.from_names(names)
        assert layout.chains["arm"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == [3, 4]

    def test_trivial(self):
        layout = JointLayout.trivial(6)
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4, 5]
        assert layout.chains["arm"].gripper == []
        assert layout.n_joints == 6

    def test_humanoid_layout(self):
        layout = JointLayout.from_config(
            {
                "torso": [0, 1, 2],
                "left_arm": {"joints": [3, 4, 5, 6, 7, 8], "gripper": [15]},
                "right_arm": {"joints": [9, 10, 11, 12, 13, 14], "gripper": [16]},
            }
        )
        assert layout.n_joints == 17
        np.testing.assert_array_equal(
            layout.joint_indices("left_arm", "right_arm"),
            [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        )
        assert layout.is_gripper(15)
        assert layout.chain_of(15) == "left_arm"

    def test_empty_chain_returns_empty(self):
        layout = JointLayout.from_config({"arm": [0, 1, 2]})
        np.testing.assert_array_equal(layout.joint_indices("nonexistent"), [])
        np.testing.assert_array_equal(layout.gripper_indices("nonexistent"), [])

    def test_names_property(self):
        layout = JointLayout.from_config(
            {"arm": [0, 1]},
            names=["shoulder", "elbow"],
        )
        assert layout.names == ["shoulder", "elbow"]
        assert layout.n_joints == 2

    def test_chain_names(self):
        layout = JointLayout.from_config(
            {
                "torso": [0],
                "left_arm": [1, 2],
                "right_arm": [3, 4],
            }
        )
        assert layout.chain_names == ["torso", "left_arm", "right_arm"]

    def test_invalid_config_raises(self):
        with pytest.raises(ValueError, match="Invalid chain config"):
            JointLayout.from_config({"arm": "bad"})  # type: ignore[dict-item]
