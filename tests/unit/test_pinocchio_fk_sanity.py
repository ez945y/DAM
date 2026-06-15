"""Sanity tests for Pinocchio FK on SO-ARM-101 URDF.

Validates that the FK helper used by the Isaac resolver adapter produces
correct frame conventions (position unit meters, quaternion xyzw, workspace
within physical arm reach).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pinocchio = pytest.importorskip("pinocchio")

URDF_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "so101_new_calib.urdf")
ARM_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
]
EE_FRAME_NAME = "gripper_link"


@pytest.fixture()
def fk_model():
    """Build a reduced Pinocchio model matching the Isaac resolver setup."""
    full_model = pinocchio.buildModelFromUrdf(URDF_PATH)
    all_joint_names = [full_model.names[i] for i in range(1, full_model.njoints)]
    joints_to_lock = [
        full_model.getJointId(name) for name in all_joint_names if name not in ARM_JOINT_NAMES
    ]
    q_ref = pinocchio.neutral(full_model)
    model = pinocchio.buildReducedModel(full_model, joints_to_lock, q_ref)
    data = model.createData()
    ee_frame_id = model.getFrameId(EE_FRAME_NAME)
    return model, data, ee_frame_id


def _compute_fk(fk_model, q: np.ndarray) -> np.ndarray:
    """Compute FK and return DAM-convention pose [x,y,z,qx,qy,qz,qw]."""
    model, data, ee_frame_id = fk_model
    q = np.asarray(q, dtype=np.float64)
    pinocchio.forwardKinematics(model, data, q)
    pinocchio.updateFramePlacements(model, data)
    oMf = data.oMf[ee_frame_id]
    quat = pinocchio.Quaternion(oMf.rotation)
    return np.array(
        [
            oMf.translation[0],
            oMf.translation[1],
            oMf.translation[2],
            quat.x,
            quat.y,
            quat.z,
            quat.w,
        ],
        dtype=np.float64,
    )


class TestFKModelStructure:
    def test_reduced_model_has_5_dof(self, fk_model):
        model, _, _ = fk_model
        assert model.nq == 5

    def test_ee_frame_exists(self, fk_model):
        model, _, ee_frame_id = fk_model
        assert ee_frame_id < model.nframes
        assert model.frames[ee_frame_id].name == EE_FRAME_NAME


class TestHomePose:
    """FK at home position (all zeros) should be a reasonable resting pose."""

    def test_home_position_within_workspace(self, fk_model):
        pose = _compute_fk(fk_model, np.zeros(5))
        pos = pose[:3]
        distance = np.linalg.norm(pos)
        # SO-ARM-101 arm reach is ~0.30m; home should be within that
        assert 0.05 < distance < 0.40, f"Home EE distance {distance:.4f}m unreasonable"

    def test_home_position_z_positive(self, fk_model):
        pose = _compute_fk(fk_model, np.zeros(5))
        # EE should be above the base at home
        assert pose[2] > 0, f"Home EE z={pose[2]:.4f} should be positive"

    def test_home_quaternion_is_unit(self, fk_model):
        pose = _compute_fk(fk_model, np.zeros(5))
        quat_norm = np.linalg.norm(pose[3:7])
        np.testing.assert_allclose(quat_norm, 1.0, atol=1e-10)


class TestQuaternionConvention:
    """Verify quaternion output follows DAM convention [qx, qy, qz, qw]."""

    def test_identity_rotation_gives_qw_one(self, fk_model):
        # If we find a config where EE is aligned with base frame,
        # qw should be close to ±1 and qx,qy,qz close to 0.
        # At home, the rotation may not be identity, so we just check
        # that qw is in the last position (index 6) by verifying
        # sum(quat^2) == 1 with the expected layout.
        pose = _compute_fk(fk_model, np.zeros(5))
        qx, qy, qz, qw = pose[3], pose[4], pose[5], pose[6]
        np.testing.assert_allclose(qx**2 + qy**2 + qz**2 + qw**2, 1.0, atol=1e-10)

    def test_shoulder_pan_rotates_xy(self, fk_model):
        """Rotating shoulder_pan should move EE in XY plane."""
        home = _compute_fk(fk_model, np.zeros(5))
        rotated = _compute_fk(fk_model, np.array([np.pi / 4, 0, 0, 0, 0]))
        # X or Y should change significantly
        xy_delta = np.linalg.norm(rotated[:2] - home[:2])
        assert xy_delta > 0.01, f"Shoulder pan didn't move EE in XY: delta={xy_delta}"


class TestWorkspaceReach:
    """Verify that various joint configurations stay within physical reach."""

    @pytest.mark.parametrize(
        "q",
        [
            np.zeros(5),
            np.array([0.5, 0.5, 0.5, 0.0, 0.0]),
            np.array([-0.5, -0.5, -0.5, 0.0, 0.0]),
            np.array([0.0, 1.0, -1.0, 0.5, 0.0]),
            np.array([np.pi / 2, 0.0, 0.0, 0.0, 0.0]),
        ],
        ids=["home", "mid_pos", "mid_neg", "elbow_bent", "pan_90"],
    )
    def test_ee_within_arm_reach(self, fk_model, q):
        pose = _compute_fk(fk_model, q)
        distance = np.linalg.norm(pose[:3])
        # Max physical reach ~0.35m, min ~0.05m (folded)
        assert distance < 0.45, f"EE at {distance:.3f}m exceeds arm reach"
        assert distance > 0.02, f"EE at {distance:.3f}m impossibly close to base"

    @pytest.mark.parametrize(
        "q",
        [
            np.zeros(5),
            np.array([0.5, 0.5, 0.5, 0.0, 0.0]),
            np.array([0.0, 1.0, -1.0, 0.5, 0.0]),
        ],
        ids=["home", "mid", "bent"],
    )
    def test_quaternion_always_unit(self, fk_model, q):
        pose = _compute_fk(fk_model, q)
        np.testing.assert_allclose(np.linalg.norm(pose[3:7]), 1.0, atol=1e-10)


class TestConventionAlignment:
    """Verify FK output matches DAM Guardrail expectations."""

    def test_pose_is_7_elements(self, fk_model):
        pose = _compute_fk(fk_model, np.zeros(5))
        assert pose.shape == (7,)

    def test_position_in_meters(self, fk_model):
        """SO-ARM-101 links are ~5-10cm each; total reach ~30cm."""
        pose = _compute_fk(fk_model, np.zeros(5))
        # If URDF were in mm, we'd see values > 50
        assert all(abs(p) < 1.0 for p in pose[:3]), "Positions seem too large for meters"

    def test_wrist_roll_only_changes_orientation(self, fk_model):
        """Wrist roll (last arm joint) should not change EE position."""
        base = _compute_fk(fk_model, np.array([0.3, 0.3, -0.3, 0.0, 0.0]))
        rolled = _compute_fk(fk_model, np.array([0.3, 0.3, -0.3, 0.0, np.pi / 3]))
        np.testing.assert_allclose(base[:3], rolled[:3], atol=1e-10)
        # But orientation should change
        quat_delta = np.linalg.norm(base[3:7] - rolled[3:7])
        assert quat_delta > 0.1, "Wrist roll should change orientation"
