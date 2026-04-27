from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class KinematicsResolver:
    """Standard Kinematics Resolver for DAM.

    Wraps Pinocchio to provide Forward Kinematics (FK) and coordinate transformations.
    """

    def __init__(
        self,
        urdf_path: str,
        controlled_joints: list[str] | None = None,
        ee_link_name: str = "gripper_link",
    ) -> None:
        try:
            import pinocchio as pin
        except ImportError:
            logger.error("KinematicsResolver requires 'pinocchio' dependency.")
            raise

        self._controlled_joints = controlled_joints or [
            "shoulder_pan",
            "shoulder_lift",
            "elbow_flex",
            "wrist_flex",
            "wrist_roll",
        ]

        # 1. Build Reduced Model
        full_model = pin.buildModelFromUrdf(urdf_path)
        all_joint_names = [full_model.names[i] for i in range(1, full_model.njoints)]
        joints_to_lock = [
            full_model.getJointId(name)
            for name in all_joint_names
            if name not in self._controlled_joints
        ]

        q_reference = pin.neutral(full_model)
        self.model = pin.buildReducedModel(full_model, joints_to_lock, q_reference)
        self.data = self.model.createData()
        self.ee_frame_id = self.model.getFrameId(ee_link_name)

        logger.info(
            "KinematicsResolver initialised with URDF: %s (EE: %s)", urdf_path, ee_link_name
        )

    def compute_fk(self, joint_positions: np.ndarray) -> np.ndarray:
        """Compute EE pose [x, y, z, qx, qy, qz, qw] from joint positions (radians)."""
        import pinocchio as pin

        # Ensure we only take the joints the model expects
        q = joint_positions[: self.model.nq].astype(np.float64)

        pin.forwardKinematics(self.model, self.data, q)
        pin.updateFramePlacements(self.model, self.data)

        o_mf = self.data.oMf[self.ee_frame_id]
        quat = pin.Quaternion(o_mf.rotation)

        return np.array([*o_mf.translation, quat.x, quat.y, quat.z, quat.w], dtype=np.float64)
