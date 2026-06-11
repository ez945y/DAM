"""KinematicsResolver — name-based observation alignment for FK/IK.

The observation vector lists every motor (SO-ARM: 5 arm joints + gripper);
the reduced pinocchio model covers only the arm joints. These tests pin the
contract that modelled joints are gathered by *name* from wherever they sit
in the observation, and that IK scatters its solution back into the full
observation-width vector leaving unmodelled joints untouched.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytest.importorskip("pinocchio")

from dam.kinematics.resolver import KinematicsResolver

URDF_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "so101_new_calib.urdf")
ARM_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
# Modelled joints deliberately NOT the leading entries: gripper first,
# arm joints reversed.
SCRAMBLED_NAMES = ["gripper", *reversed(ARM_JOINT_NAMES)]

Q_ARM = np.array([0.3, -0.2, 0.4, 0.1, -0.5])
GRIPPER_POS = 0.7


def _scrambled_obs(q_arm: np.ndarray, gripper: float) -> np.ndarray:
    """Build an observation vector laid out per SCRAMBLED_NAMES."""
    by_name = dict(zip(ARM_JOINT_NAMES, q_arm, strict=True), gripper=gripper)
    return np.array([by_name[n] for n in SCRAMBLED_NAMES])


@pytest.fixture(scope="module")
def baseline() -> KinematicsResolver:
    """Resolver without names — positional truncation, the legacy behaviour."""
    return KinematicsResolver(URDF_PATH)


@pytest.fixture(scope="module")
def scrambled() -> KinematicsResolver:
    """Resolver whose observation layout puts the modelled joints last."""
    return KinematicsResolver(URDF_PATH, observation_joint_names=SCRAMBLED_NAMES)


class TestForwardKinematics:
    def test_model_joint_names_match_urdf_order(self, baseline: KinematicsResolver) -> None:
        assert baseline.model_joint_names == ARM_JOINT_NAMES

    def test_positional_fallback_unchanged(self, baseline: KinematicsResolver) -> None:
        # Legacy layout (arm joints leading, gripper trailing) still works
        # without names.
        pose_trunc = baseline.compute_fk(Q_ARM)
        pose_full = baseline.compute_fk(np.append(Q_ARM, GRIPPER_POS))
        np.testing.assert_allclose(pose_full, pose_trunc)

    def test_name_based_gather_with_non_leading_joints(
        self, baseline: KinematicsResolver, scrambled: KinematicsResolver
    ) -> None:
        pose_named = scrambled.compute_fk(_scrambled_obs(Q_ARM, GRIPPER_POS))
        pose_ref = baseline.compute_fk(Q_ARM)
        np.testing.assert_allclose(pose_named, pose_ref, atol=1e-12)

    def test_name_based_matches_leading_layout(self, baseline: KinematicsResolver) -> None:
        # Names that mirror the positional layout must reproduce it exactly.
        resolver = KinematicsResolver(
            URDF_PATH, observation_joint_names=[*ARM_JOINT_NAMES, "gripper"]
        )
        obs = np.append(Q_ARM, GRIPPER_POS)
        np.testing.assert_allclose(resolver.compute_fk(obs), baseline.compute_fk(obs))

    def test_missing_model_joint_falls_back_positionally(
        self, baseline: KinematicsResolver
    ) -> None:
        resolver = KinematicsResolver(
            URDF_PATH, observation_joint_names=["gripper", "unrelated_joint"]
        )
        obs = np.append(Q_ARM, GRIPPER_POS)
        np.testing.assert_allclose(resolver.compute_fk(obs), baseline.compute_fk(obs))

    def test_set_names_after_init(self, baseline: KinematicsResolver) -> None:
        resolver = KinematicsResolver(URDF_PATH)
        resolver.set_observation_joint_names(SCRAMBLED_NAMES)
        pose = resolver.compute_fk(_scrambled_obs(Q_ARM, GRIPPER_POS))
        np.testing.assert_allclose(pose, baseline.compute_fk(Q_ARM), atol=1e-12)
        resolver.set_observation_joint_names(None)
        np.testing.assert_allclose(resolver.compute_fk(Q_ARM), baseline.compute_fk(Q_ARM))

    def test_forward_kinematics_alias(self, baseline: KinematicsResolver) -> None:
        np.testing.assert_allclose(baseline.forward_kinematics(Q_ARM), baseline.compute_fk(Q_ARM))


class TestInverseKinematics:
    def test_round_trip_with_scrambled_layout(
        self, baseline: KinematicsResolver, scrambled: KinematicsResolver
    ) -> None:
        target_pose = baseline.compute_fk(Q_ARM)
        seed = _scrambled_obs(np.zeros(5), GRIPPER_POS)

        result = scrambled.inverse_kinematics(target_pose, seed)

        assert result.shape == seed.shape
        # Unmodelled gripper keeps its seed value.
        assert result[0] == pytest.approx(GRIPPER_POS)
        # The solved pose reaches the target (the target is reachable by
        # construction; allow solver slack on position).
        reached = scrambled.compute_fk(result)
        np.testing.assert_allclose(reached[:3], target_pose[:3], atol=1e-3)

    def test_positional_fallback_keeps_trailing_gripper(self, baseline: KinematicsResolver) -> None:
        target_pose = baseline.compute_fk(Q_ARM)
        seed = np.append(np.zeros(5), GRIPPER_POS)

        result = baseline.inverse_kinematics(target_pose, seed)

        assert result.shape == seed.shape
        assert result[-1] == pytest.approx(GRIPPER_POS)
        reached = baseline.compute_fk(result)
        np.testing.assert_allclose(reached[:3], target_pose[:3], atol=1e-3)

    def test_scrambled_solution_matches_positional_solution(
        self, baseline: KinematicsResolver, scrambled: KinematicsResolver
    ) -> None:
        # Same problem expressed in both layouts must yield the same arm
        # solution — only the storage order differs.
        target_pose = baseline.compute_fk(Q_ARM)
        q_pos = baseline.inverse_kinematics(target_pose, np.append(np.zeros(5), GRIPPER_POS))
        q_scr = scrambled.inverse_kinematics(target_pose, _scrambled_obs(np.zeros(5), GRIPPER_POS))
        # q_scr layout: [gripper, wrist_roll, ..., shoulder_pan]
        np.testing.assert_allclose(q_scr[1:][::-1], q_pos[:5], atol=1e-9)
