"""LeRobotAdapter — unified hardware adapter for Reading and Writing to lerobot robots.

This class implements both SensorAdapter and ActionAdapter, allowing a single
connection to the physical hardware to serve as both the observation source
and the action sink. This is the preferred way to interface with motor-based
hardware that shares a single communication bus/node.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import numpy as np

from dam.adapter.base import ActionAdapter, SensorAdapter
from dam.types.action import ValidatedAction
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class LeRobotAdapter(SensorAdapter, ActionAdapter):
    """Unified adapter for lerobot robots (SO-ARM101, Koch, …).

    Acts as both a SensorAdapter (reading positions/images) and an
    ActionAdapter (sending motor commands).
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
        # Default order matching so101_follower preset
        from dam.adapter.lerobot.source import _DEFAULT_JOINT_NAMES

        self._joint_names: list[str] = joint_names or list(_DEFAULT_JOINT_NAMES)
        self._degrees_mode = degrees_mode
        self._obs_hz = obs_hz

        # Sensor state
        n_joints = len(self._joint_names)
        self._prev_positions: np.ndarray = np.zeros(n_joints, dtype=np.float64)
        self._prev_velocities: np.ndarray = np.zeros(n_joints, dtype=np.float64)
        self._prev_images: dict[str, np.ndarray] = {}
        self._prev_ee_pose: np.ndarray | None = None
        self._prev_time: float | None = None

        # Sink state
        self._last_action: ValidatedAction | None = None

        self._connected = False

        # Pinocchio FK
        self._pin_model = None
        self._pin_data = None
        self._pin_ee_frame_id: int | None = None
        if urdf_path is not None:
            self._init_pinocchio(urdf_path)

    def _init_pinocchio(self, urdf_path: str) -> None:
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
            logger.info("LeRobotAdapter: pinocchio FK initialized from %s", urdf_path)
        except Exception as exc:
            logger.warning("LeRobotAdapter: pinocchio FK unavailable — %s", exc)

    # ── Shared Lifecycle ────────────────────────────────────────────────────

    def connect(self) -> None:
        if not self._connected:
            if hasattr(self._robot, "connect"):
                self._robot.connect()
            self._connected = True
            self._prev_time = time.monotonic()
            logger.info(
                "LeRobotAdapter connected  joints=%s  degrees_mode=%s",
                self._joint_names,
                self._degrees_mode,
            )

    def disconnect(self) -> None:
        if self._connected:
            self._connected = False
            if self._robot is not None:
                try:
                    if hasattr(self._robot, "disconnect"):
                        self._robot.disconnect()
                    elif hasattr(self._robot, "close"):
                        self._robot.close()
                except Exception as e:
                    logger.debug("LeRobotAdapter: robot disconnect failed: %s", e)
            logger.info("LeRobotAdapter disconnected")

    def is_healthy(self) -> bool:
        return self._connected and self._robot is not None

    def get_hardware_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "connected": self._connected,
            "latency_ms": (time.monotonic() - self._prev_time) * 1000 if self._prev_time else 0,
        }
        if hasattr(self._robot, "get_state"):
            status["robot_state"] = self._robot.get_state()
        return status

    # ── SensorAdapter Interface (Read) ──────────────────────────────────────

    def read(self) -> Observation:
        if not self._connected:
            try:
                self.connect()
            except Exception as e:
                logger.error("LeRobotAdapter: auto-connect failed: %s", e)

        now = time.monotonic()
        try:
            if hasattr(self._robot, "get_observation"):
                raw = self._robot.get_observation()
            else:
                raw = self._robot.capture_observation()

            return self._convert_obs(raw)
        except Exception as e:
            logger.error("LeRobotAdapter read failure: %s", e)
            # Use now if we haven't successfully read anything yet (bootstrap)
            fallback_ts = self._prev_time if self._prev_time is not None else now
            return Observation(
                timestamp=fallback_ts,
                joint_positions=self._prev_positions.copy(),
                joint_velocities=self._prev_velocities.copy(),
                end_effector_pose=self._prev_ee_pose,
                images=self._prev_images.copy(),
                metadata={"hardware_status": {"fault": str(e)}},
            )

    def _convert_obs(self, raw: dict[str, Any]) -> Observation:
        now = time.monotonic()

        # 1. Detect if it's modern dict or legacy tensor
        if any(k.endswith(".pos") for k in raw):
            # Modern API
            pos_list = []
            for name in self._joint_names:
                val = float(raw.get(f"{name}.pos", 0.0))
                if self._degrees_mode and "gripper" not in name.lower():
                    val = math.radians(val)
                pos_list.append(val)
            positions = np.array(pos_list, dtype=np.float64)

            # Velocities
            if any(f"{n}.vel" in raw for n in self._joint_names):
                vel_list = []
                for name in self._joint_names:
                    val = float(raw.get(f"{name}.vel", 0.0))
                    if self._degrees_mode and "gripper" not in name.lower():
                        val = math.radians(val)
                    vel_list.append(val)
                velocities = np.array(vel_list, dtype=np.float64)
            else:
                velocities = self._estimate_velocity(positions, now)
        else:
            # Legacy Tensor API
            state = raw.get("observation.state", raw.get("state"))
            positions = np.asarray(state, dtype=np.float64).flatten()
            vel_raw = raw.get("observation.velocity", raw.get("velocity"))
            if vel_raw is not None:
                velocities = np.asarray(vel_raw, dtype=np.float64).flatten()
            else:
                velocities = self._estimate_velocity(positions, now)

        # Update cache
        self._prev_positions = positions.copy()
        self._prev_velocities = velocities.copy()

        # Images — persist them in _prev_images for robustness
        if hasattr(self._robot, "cameras") and self._robot.cameras:
            for cam_name, cam in self._robot.cameras.items():
                try:
                    frame = cam.async_read()
                    if frame is not None:
                        self._prev_images[cam_name] = np.asarray(frame).copy()
                except Exception:
                    pass

        # EE Pose via Pinocchio
        ee_pose = self._compute_ee_pose(positions)
        self._prev_ee_pose = ee_pose

        # Update heartbeat clock only AFTER successful conversion
        self._prev_time = now

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=self._prev_images.copy(),
        )

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if self._prev_time is not None:
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt
        return np.zeros_like(positions)

    def _compute_ee_pose(self, positions_rad: np.ndarray) -> np.ndarray | None:
        if self._pin_model is None:
            return None
        try:
            import pinocchio as pin

            q = positions_rad[: self._pin_model.nq].astype(np.float64)
            pin.forwardMotions(self._pin_model, self._pin_data, q)
            pin.updateFramePlacements(self._pin_model, self._pin_data)
            oMf = self._pin_data.oMf[self._pin_ee_frame_id]
            quat = pin.Quaternion(oMf.rotation)
            return np.array([*oMf.translation, quat.x, quat.y, quat.z, quat.w])
        except Exception:
            return None

    # ── ActionAdapter Interface (Write) ─────────────────────────────────────

    def apply(self, action: ValidatedAction) -> None:
        self._last_action = action
        # 1. Convert ValidatedAction (rad) to LeRobot dict (deg)
        positions = np.asarray(action.target_joint_positions, dtype=np.float64)
        n = min(len(positions), len(self._joint_names))

        action_dict: dict[str, Any] = {}
        for i in range(n):
            name = self._joint_names[i]
            val = float(positions[i])
            if self._degrees_mode and "gripper" not in name.lower():
                val = math.degrees(val)
            action_dict[f"{name}.pos"] = val

        if action.gripper_action is not None and "gripper" in self._joint_names:
            action_dict["gripper.pos"] = float(action.gripper_action)

        self._robot.send_action(action_dict)

    def emergency_stop(self) -> None:
        logger.error("LeRobotAdapter: EMERGENCY STOP")
        if hasattr(self._robot, "emergency_stop"):
            self._robot.emergency_stop()

    @property
    def last_action(self) -> ValidatedAction | None:
        return self._last_action
