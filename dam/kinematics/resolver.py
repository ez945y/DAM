from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class KinematicsResolver:
    """Standard Kinematics Resolver for DAM.

    Wraps Pinocchio to provide Forward Kinematics (FK), Inverse Kinematics
    (IK) and coordinate transformations.

    Observation alignment: when ``observation_joint_names`` is provided, the
    modelled joints are gathered from / scattered into the observation vector
    by joint name (same contract as ``DynamicsContext.q_indices_for``), so the
    URDF joints may sit anywhere in the observation. Without names the
    resolver falls back to positional truncation — the model's joints are
    assumed to be the leading observation entries.
    """

    def __init__(
        self,
        urdf_path: str,
        controlled_joints: list[str] | None = None,
        ee_link_name: str = "gripper_link",
        observation_joint_names: list[str] | None = None,
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
        self.model_joint_names: list[str] = [
            self.model.names[i] for i in range(1, self.model.njoints)
        ]

        self._obs_joint_names: list[str] | None = None
        self._obs_indices: np.ndarray | None = None
        self.set_observation_joint_names(observation_joint_names)

        logger.info(
            "KinematicsResolver initialised with URDF: %s (EE: %s)", urdf_path, ee_link_name
        )

    # ── Observation ↔ model q alignment ────────────────────────────────────

    def set_observation_joint_names(self, names: list[str] | None) -> None:
        """Declare the observation vector's joint order (e.g. from the preset
        joint layout) so FK/IK can gather modelled joints by name.

        Passing None — or a list missing any model joint — clears the mapping
        and restores positional truncation.
        """
        self._obs_joint_names = list(names) if names else None
        self._obs_indices = None
        if self._obs_joint_names is None:
            return
        if self.model.nq != len(self.model_joint_names):
            # Multi-DOF joints break the one-name-per-q assumption.
            logger.warning(
                "KinematicsResolver: model has multi-DOF joints (nq=%d, joints=%d); "
                "falling back to positional q alignment.",
                self.model.nq,
                len(self.model_joint_names),
            )
            return
        name_to_obs = {n: i for i, n in enumerate(self._obs_joint_names)}
        try:
            self._obs_indices = np.array(
                [name_to_obs[n] for n in self.model_joint_names], dtype=np.intp
            )
        except KeyError as missing:
            logger.warning(
                "KinematicsResolver: model joint %s not in observation names %s; "
                "falling back to positional q alignment.",
                missing,
                self._obs_joint_names,
            )

    def _gather_q(self, joint_positions: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        """Extract the model's q from a full observation-width joint vector.

        Returns ``(q, idx)`` where ``idx`` is the observation index of each q
        entry, or None when the gather was positional.
        """
        q_obs = np.asarray(joint_positions, dtype=np.float64).flatten()
        idx = self._obs_indices
        if idx is not None and q_obs.shape[0] > int(idx.max()):
            return q_obs[idx], idx
        return q_obs[: self.model.nq], None

    # ── Forward kinematics ──────────────────────────────────────────────────

    def compute_fk(self, joint_positions: np.ndarray) -> np.ndarray:
        """Compute EE pose [x, y, z, qx, qy, qz, qw] from joint positions (radians)."""
        import pinocchio as pin

        q, _ = self._gather_q(joint_positions)

        pin.forwardKinematics(self.model, self.data, q)
        pin.updateFramePlacements(self.model, self.data)

        o_mf = self.data.oMf[self.ee_frame_id]
        quat = pin.Quaternion(o_mf.rotation)

        return np.array([*o_mf.translation, quat.x, quat.y, quat.z, quat.w], dtype=np.float64)

    def forward_kinematics(self, joint_positions: np.ndarray) -> np.ndarray:
        """Protocol-compatible alias for :meth:`compute_fk`."""
        return self.compute_fk(joint_positions)

    # ── Inverse kinematics ──────────────────────────────────────────────────

    def inverse_kinematics(
        self,
        target_ee_pose: np.ndarray,
        current_joint_positions: np.ndarray,
        *,
        max_iters: int = 300,
        tol: float = 1e-4,
        damping: float = 1e-9,
        step: float = 0.2,
    ) -> np.ndarray:
        """Damped-least-squares IK towards ``target_ee_pose`` [x,y,z,qx,qy,qz,qw].

        Returns a joint vector of the same width as ``current_joint_positions``:
        modelled joints carry the IK solution, unmodelled joints (e.g. the
        gripper) keep their current values.
        """
        import pinocchio as pin

        target = np.asarray(target_ee_pose, dtype=np.float64).flatten()
        o_m_des = pin.XYZQUATToSE3(target[:7])

        full = np.asarray(current_joint_positions, dtype=np.float64).flatten().copy()
        q, idx = self._gather_q(full)
        q = q.copy()

        identity6 = np.eye(6)
        for _ in range(max_iters):
            pin.forwardKinematics(self.model, self.data, q)
            pin.updateFramePlacements(self.model, self.data)
            i_m_des = self.data.oMf[self.ee_frame_id].actInv(o_m_des)
            err = pin.log(i_m_des).vector
            if float(np.linalg.norm(err)) < tol:
                break
            J = pin.computeFrameJacobian(self.model, self.data, q, self.ee_frame_id, pin.LOCAL)
            J = -np.asarray(pin.Jlog6(i_m_des.inverse())) @ J
            dq = -(J.T @ np.linalg.solve(J @ J.T + damping * identity6, err))
            q = pin.integrate(self.model, q, dq * step)

        if idx is not None:
            full[idx] = q
        else:
            full[: self.model.nq] = q
        return full
