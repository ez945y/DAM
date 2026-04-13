"""LeRobotSourceAdapter — bridges lerobot robot.get_observation() to DAM Observation.

Supports both modern and legacy lerobot robot APIs:

Modern (lerobot ≥ 0.2, ``get_observation()``)
----------------------------------------------
Returns a flat dict of named joint values **in degrees**::

    {"shoulder_pan.pos": -12.3, "shoulder_pan.vel": 0.1, ...}

Camera frames are read via ``robot.cameras[name].async_read()``.

Legacy (lerobot < 0.2, ``capture_observation()``)
-------------------------------------------------
Returns a dict with tensor-valued keys::

    {"observation.state": tensor[n_joints], "observation.images.top": tensor[H,W,C]}

DAM Observation always stores joint angles in **radians**.
The source adapter converts degrees → radians when ``degrees_mode=True``
(all SO-ARM and Koch presets operate in degrees internally).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import numpy as np

from dam.adapter.base import SensorAdapter
from dam.types.observation import Observation

logger = logging.getLogger(__name__)

_DEFAULT_JOINT_NAMES: list[str] = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


class LeRobotSourceAdapter(SensorAdapter):
    """SensorAdapter implementation for lerobot robots (SO-ARM101, Koch, …).

    Parameters
    ----------
    robot:
        Any object that implements either ``get_observation()`` (modern API)
        or ``capture_observation()`` (legacy API).  Duck-typed so tests can
        inject a simple mock without installing lerobot.
    joint_names:
        Ordered list of joint names.  Defaults to so101 joint order.
        Must match the order the robot returns joint states.
    degrees_mode:
        If True, the robot returns positions in degrees; they are converted
        to radians before creating the DAM Observation.  Set to False if the
        robot already speaks radians.
    obs_hz:
        Expected observation rate — used only for velocity estimation when
        the robot does not provide velocities directly.
    """

    # SO-101 arm joints in pinocchio order (excludes gripper)
    _ARM_JOINT_NAMES: list[str] = [
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
    ]

    def __init__(
        self,
        robot: Any,
        joint_names: list[str] | None = None,
        degrees_mode: bool = True,
        obs_hz: float = 50.0,
        urdf_path: str | None = None,
    ) -> None:
        self._robot = robot
        self._joint_names: list[str] = joint_names or list(_DEFAULT_JOINT_NAMES)
        self._degrees_mode = degrees_mode
        self._obs_hz = obs_hz
        self._prev_positions: np.ndarray | None = None
        self._prev_time: float | None = None
        self._connected = False

        # Pinocchio FK (optional — initialised only when urdf_path is provided)
        self._pin_model = None
        self._pin_data = None
        self._pin_ee_frame_id: int | None = None
        if urdf_path is not None:
            self._init_pinocchio(urdf_path)

    def _init_pinocchio(self, urdf_path: str) -> None:
        """Load URDF and build a reduced pinocchio model for the 5 arm joints."""
        try:
            import pinocchio as pin

            full_model = pin.buildModelFromUrdf(urdf_path)
            all_names = [full_model.names[i] for i in range(1, full_model.njoints)]
            lock_ids = [
                full_model.getJointId(n) for n in all_names if n not in self._ARM_JOINT_NAMES
            ]
            q_ref = pin.neutral(full_model)
            self._pin_model = pin.buildReducedModel(full_model, lock_ids, q_ref)
            self._pin_data = self._pin_model.createData()
            self._pin_ee_frame_id = self._pin_model.getFrameId("gripper_link")
            logger.info("LeRobotSourceAdapter: pinocchio FK initialised from %s", urdf_path)
        except Exception as exc:
            logger.warning("LeRobotSourceAdapter: pinocchio FK unavailable — %s", exc)

    def _compute_ee_pose(self, positions_rad: np.ndarray) -> np.ndarray | None:
        """Run forward motions and return [x,y,z,qx,qy,qz,qw] or None."""
        if self._pin_model is None:
            return None
        try:
            import pinocchio as pin

            # Take first N arm joints (positions_rad is already in radians)
            n_arm = self._pin_model.nq
            q = positions_rad[:n_arm].astype(np.float64)
            pin.forwardMotions(self._pin_model, self._pin_data, q)
            pin.updateFramePlacements(self._pin_model, self._pin_data)
            oMf = self._pin_data.oMf[self._pin_ee_frame_id]
            quat = pin.Quaternion(oMf.rotation)
            return np.array(
                [*oMf.translation, quat.x, quat.y, quat.z, quat.w],
                dtype=np.float64,
            )
        except Exception as exc:
            logger.debug("FK computation failed: %s", exc)
            return None

    # ── SensorAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        if hasattr(self._robot, "connect"):
            self._robot.connect()
        self._connected = True
        logger.info(
            "LeRobotSourceAdapter connected  joints=%s  degrees_mode=%s",
            self._joint_names,
            self._degrees_mode,
        )

    def read(self) -> Observation:
        """Read one observation from the robot and return a DAM Observation."""
        try:
            if hasattr(self._robot, "get_observation"):
                raw = self._robot.get_observation()
            else:
                raw = self._robot.capture_observation()
            return self._convert(raw)
        except Exception as e:
            logger.error("LeRobotSourceAdapter hardware read failure: %s", e)
            # Create a fallback Observation with hardware_status fault
            import time

            return Observation(
                timestamp=time.perf_counter(),
                joint_positions=self._prev_positions,
                metadata={
                    "hardware_status": {"error_codes": [-1], "reason": f"Hardware read error: {e}"}
                },
            )

    def is_healthy(self) -> bool:
        return self._connected and self._robot is not None

    def disconnect(self) -> None:
        self._connected = False
        if self._robot is not None:
            try:
                if hasattr(self._robot, "disconnect"):
                    self._robot.disconnect()
                elif hasattr(self._robot, "close"):
                    self._robot.close()
            except Exception as e:
                logger.debug("LeRobotSourceAdapter: robot disconnect/close failed: %s", e)
        logger.info("LeRobotSourceAdapter disconnected")

    # ── Internal dispatch ──────────────────────────────────────────────────

    def _convert(self, raw: dict[str, Any]) -> Observation:
        if any(k.endswith(".pos") for k in raw):
            return self._convert_named(raw)
        return self._convert_legacy(raw)

    # ── Modern API: named-joint dict ───────────────────────────────────────

    def _convert_named(self, raw: dict[str, Any]) -> Observation:
        """Convert ``{joint.pos: degrees}`` dict to DAM Observation (radians)."""
        now = time.monotonic()

        # Joint positions
        pos_list: list[float] = []
        for name in self._joint_names:
            raw_val = float(raw.get(f"{name}.pos", 0.0))
            if self._degrees_mode and not self._is_gripper_joint(name):
                pos_list.append(math.radians(raw_val))
            else:
                pos_list.append(raw_val)
        positions = np.array(pos_list, dtype=np.float64)

        # Joint velocities (degrees/s → rad/s if available)
        has_vel = any(f"{n}.vel" in raw for n in self._joint_names)
        if has_vel:
            vel_list: list[float] = []
            for name in self._joint_names:
                raw_val = float(raw.get(f"{name}.vel", 0.0))
                if self._degrees_mode and not self._is_gripper_joint(name):
                    vel_list.append(math.radians(raw_val))
                else:
                    vel_list.append(raw_val)
            velocities: np.ndarray | None = np.array(vel_list, dtype=np.float64)
        else:
            velocities = self._estimate_velocity(positions, now)

        self._prev_positions = positions.copy()
        self._prev_time = now

        # Camera frames — read from robot.cameras if present
        images: dict[str, np.ndarray] | None = None
        if hasattr(self._robot, "cameras") and self._robot.cameras:
            images = {}
            for cam_name, cam in self._robot.cameras.items():
                try:
                    frame = cam.async_read()
                    if frame is not None:
                        images[cam_name] = np.asarray(frame)
                except Exception as e:
                    logger.debug("Camera '%s' read error: %s", cam_name, e)
            if not images:
                images = None

        # Compute EE pose via pinocchio FK when a URDF was supplied;
        # falls back to None so guards skip the workspace check gracefully.
        ee_pose = self._compute_ee_pose(positions)

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=images,
        )

    # ── Legacy API: observation.state tensor ──────────────────────────────

    def _convert_legacy(self, raw: dict[str, Any]) -> Observation:
        """Convert ``{"observation.state": tensor}`` dict to DAM Observation."""
        now = time.monotonic()

        state = raw.get("observation.state", raw.get("state"))
        if state is None:
            raise KeyError(
                "LeRobot obs dict missing 'observation.state'. "
                "If using modern lerobot, ensure get_observation() is available."
            )
        positions = np.asarray(state, dtype=np.float64).flatten()

        vel_raw = raw.get("observation.velocity", raw.get("velocity"))
        if vel_raw is not None:
            velocities: np.ndarray | None = np.asarray(vel_raw, dtype=np.float64).flatten()
        else:
            velocities = self._estimate_velocity(positions, now)

        self._prev_positions = positions.copy()
        self._prev_time = now

        ee_raw = raw.get("observation.end_effector_pose")
        ee_pose: np.ndarray
        if ee_raw is not None:
            ee_pose = np.asarray(ee_raw, dtype=np.float64).flatten()
        else:
            ee_pose = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)

        images: dict[str, np.ndarray] | None = None
        for key, val in raw.items():
            if key.startswith("observation.images."):
                if images is None:
                    images = {}
                cam_name = key[len("observation.images.") :]
                images[cam_name] = np.asarray(val)

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=images,
        )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_gripper_joint(name: str) -> bool:
        """Gripper joints use linear units (m or normalised), not degrees."""
        return "gripper" in name.lower()

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if self._prev_positions is not None and self._prev_time is not None:
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt
        return np.zeros_like(positions)
