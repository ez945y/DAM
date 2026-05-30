"""Tests for JointLayout contract."""

import numpy as np
import pytest

from dam.types.joint_layout import JointChain, JointLayout

# ── JointChain ────────────────────────────────────────────────────────────


class TestJointChain:
    def test_all_indices(self):
        chain = JointChain(indices=[0, 1, 2], gripper=[5])
        assert chain.all_indices == [0, 1, 2, 5]

    def test_has_gripper(self):
        assert JointChain(indices=[0], gripper=[1]).has_gripper
        assert not JointChain(indices=[0]).has_gripper

    def test_repr_shows_names(self):
        chain = JointChain(
            indices=[0, 1],
            gripper=[2],
            _names=["shoulder", "elbow"],
            _gripper_names=["finger"],
        )
        r = repr(chain)
        assert "shoulder" in r
        assert "finger" in r


# ── Zero-config: from_names auto-derive ───────────────────────────────────


class TestFromNames:
    """The 80% case: just list joint_names, the system does the rest."""

    SO101 = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]

    def test_so101_auto_derive(self):
        layout = JointLayout.from_names(self.SO101)
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4]
        assert layout.chains["arm"].gripper == [5]
        assert layout.names == self.SO101

    def test_no_gripper_keyword(self):
        layout = JointLayout.from_names(["j1", "j2", "j3"])
        assert layout.chains["arm"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == []

    def test_dual_finger(self):
        layout = JointLayout.from_names(["j1", "j2", "left_finger", "right_finger"])
        assert layout.chains["arm"].indices == [0, 1]
        assert layout.chains["arm"].gripper == [2, 3]

    def test_repr_readable(self):
        layout = JointLayout.from_names(self.SO101)
        r = repr(layout)
        assert "shoulder_pan" in r
        assert "gripper" in r.lower()

    def test_summary(self):
        layout = JointLayout.from_names(self.SO101)
        assert "5j" in layout.summary
        assert "1g" in layout.summary


# ── Name-based chains: from_config ────────────────────────────────────────


class TestFromConfig:
    """Complex robots: define chains using joint names, not indices."""

    HUMANOID_NAMES = [
        "torso_y",
        "torso_p",
        "torso_r",
        "l_sh",
        "l_el",
        "l_wr",
        "l_wp",
        "r_sh",
        "r_el",
        "r_wr",
        "r_wp",
        "l_grip",
        "r_grip",
    ]

    def test_full_form_with_names(self):
        layout = JointLayout.from_config(
            {"arm": {"joints": ["shoulder", "elbow"], "gripper": ["finger"]}},
            joint_names=["shoulder", "elbow", "finger"],
        )
        assert layout.chains["arm"].indices == [0, 1]
        assert layout.chains["arm"].gripper == [2]

    def test_short_form(self):
        layout = JointLayout.from_config(
            {"torso": ["torso_y", "torso_p"]},
            joint_names=["torso_y", "torso_p", "arm_j1"],
        )
        assert layout.chains["torso"].indices == [0, 1]
        assert layout.chains["torso"].gripper == []

    def test_humanoid_multi_chain(self):
        layout = JointLayout.from_config(
            {
                "torso": ["torso_y", "torso_p", "torso_r"],
                "left_arm": {
                    "joints": ["l_sh", "l_el", "l_wr", "l_wp"],
                    "gripper": ["l_grip"],
                },
                "right_arm": {
                    "joints": ["r_sh", "r_el", "r_wr", "r_wp"],
                    "gripper": ["r_grip"],
                },
            },
            joint_names=self.HUMANOID_NAMES,
        )
        assert layout.n_joints == 13
        np.testing.assert_array_equal(
            layout.joint_indices("left_arm"),
            [3, 4, 5, 6],
        )
        np.testing.assert_array_equal(
            layout.gripper_indices("left_arm", "right_arm"),
            [11, 12],
        )
        assert layout.chains_with_gripper() == ["left_arm", "right_arm"]

    def test_backward_compat_integer_indices(self):
        layout = JointLayout.from_config(
            {"arm": {"joints": [0, 1, 2], "gripper": [3]}},
            joint_names=["j1", "j2", "j3", "grip"],
        )
        assert layout.chains["arm"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == [3]


# ── Validation: helpful errors ────────────────────────────────────────────


class TestValidation:
    def test_typo_suggests_correction(self):
        with pytest.raises(ValueError, match="Did you mean 'shoulder'"):
            JointLayout.from_config(
                {"arm": ["shouler", "elbow"]},  # typo
                joint_names=["shoulder", "elbow"],
            )

    def test_unknown_joint_no_close_match(self):
        with pytest.raises(ValueError, match="not found in joint_names"):
            JointLayout.from_config(
                {"arm": ["xyz_nonexistent"]},
                joint_names=["shoulder", "elbow"],
            )

    def test_duplicate_assignment_detected(self):
        with pytest.raises(ValueError, match="assigned to both"):
            JointLayout.from_config(
                {
                    "chain_a": ["j1", "j2"],
                    "chain_b": ["j2", "j3"],  # j2 is duplicate
                },
                joint_names=["j1", "j2", "j3"],
            )

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="expected list or dict"):
            JointLayout.from_config(
                {"arm": "bad"},  # type: ignore[dict-item]
                joint_names=["j1"],
            )


# ── Queries ───────────────────────────────────────────────────────────────


class TestQueries:
    @pytest.fixture()
    def layout(self):
        return JointLayout.from_config(
            {
                "arm": {"joints": ["j1", "j2", "j3"], "gripper": ["grip"]},
            },
            joint_names=["j1", "j2", "j3", "grip"],
        )

    def test_joint_indices(self, layout):
        np.testing.assert_array_equal(layout.joint_indices("arm"), [0, 1, 2])

    def test_gripper_indices(self, layout):
        np.testing.assert_array_equal(layout.gripper_indices("arm"), [3])

    def test_all_indices(self, layout):
        np.testing.assert_array_equal(layout.all_indices("arm"), [0, 1, 2, 3])

    def test_mask_with_gripper(self, layout):
        np.testing.assert_array_equal(
            layout.mask("arm", include_gripper=True),
            [True, True, True, True],
        )

    def test_mask_without_gripper(self, layout):
        np.testing.assert_array_equal(
            layout.mask("arm", include_gripper=False),
            [True, True, True, False],
        )

    def test_chain_of(self, layout):
        assert layout.chain_of(0) == "arm"
        assert layout.chain_of(3) == "arm"
        assert layout.chain_of(99) is None

    def test_is_gripper(self, layout):
        assert not layout.is_gripper(0)
        assert layout.is_gripper(3)

    def test_nonexistent_chain(self, layout):
        np.testing.assert_array_equal(layout.joint_indices("nope"), [])
        np.testing.assert_array_equal(layout.gripper_indices("nope"), [])

    def test_trivial(self):
        layout = JointLayout.trivial(6)
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4, 5]
        assert layout.n_joints == 6

    def test_chain_names(self):
        layout = JointLayout.from_config(
            {"torso": ["a"], "arm": ["b"]},
            joint_names=["a", "b"],
        )
        assert layout.chain_names == ["torso", "arm"]

    def test_str_is_summary(self):
        layout = JointLayout.trivial(3)
        assert str(layout) == layout.summary


# ── Preset integration ────────────────────────────────────────────────────


class TestPresetIntegration:
    def test_preset_auto_derive(self):
        from dam.preset.registry import RobotPreset

        preset = RobotPreset(
            name="test_bot",
            joint_names=["j1", "j2", "j3", "gripper"],
        )
        layout = preset.joint_layout
        assert layout.chains["arm"].indices == [0, 1, 2]
        assert layout.chains["arm"].gripper == [3]

    def test_preset_explicit_chains(self):
        from dam.preset.registry import RobotPreset

        preset = RobotPreset(
            name="dual_arm",
            joint_names=["l_sh", "l_el", "l_grip", "r_sh", "r_el", "r_grip"],
            chains={
                "left_arm": {
                    "joints": ["l_sh", "l_el"],
                    "gripper": ["l_grip"],
                },
                "right_arm": {
                    "joints": ["r_sh", "r_el"],
                    "gripper": ["r_grip"],
                },
            },
        )
        layout = preset.joint_layout
        assert len(layout.chains) == 2
        assert layout.is_gripper(2)  # l_grip
        assert layout.chain_of(2) == "left_arm"

    def test_bundled_so101(self):
        from dam.preset.registry import get_preset

        preset = get_preset("so101_follower")
        layout = preset.joint_layout
        assert layout.chains["arm"].indices == [0, 1, 2, 3, 4]
        assert layout.chains["arm"].gripper == [5]
        assert "gripper" in layout.chains["arm"]._gripper_names
