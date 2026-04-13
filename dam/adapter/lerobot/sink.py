"""LeRobotSinkAdapter — sends ValidatedAction to a lerobot robot.

Converts DAM ValidatedAction (joint angles in **radians**) to the lerobot
named-joint action dict format (**degrees** for revolute joints)::

    {"shoulder_pan.pos": -10.5, "shoulder_lift.pos": 45.0, ..., "gripper.pos": 0.02}

The gripper joint is treated as a linear/normalised value and is NOT
degree-converted — it passes through as-is.

Falls back to legacy ``{"action": tensor}`` format when robot does not
accept named-joint dicts (detected at first ``apply()`` call).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from dam.adapter.base import ActionAdapter
from dam.types.action import ValidatedAction

logger = logging.getLogger(__name__)

_DEFAULT_JOINT_NAMES: list[str] = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


class LeRobotSinkAdapter(ActionAdapter):
    """ActionAdapter implementation for lerobot robots (SO-ARM101, Koch, …).

    Parameters
    ----------
    robot:
        Any object with ``send_action(action_dict)`` — duck typed for
        testability without installing lerobot.
    joint_names:
        Ordered list of joint names matching the robot's joint order.
    degrees_mode:
        If True, convert radian values to degrees before sending.
        The gripper joint is always exempt from this conversion.
    """

    def __init__(
        self,
        robot: Any,
        joint_names: list[str] | None = None,
        degrees_mode: bool = True,
    ) -> None:
        self._robot = robot
        self._joint_names: list[str] = joint_names or list(_DEFAULT_JOINT_NAMES)
        self._degrees_mode = degrees_mode
        self._last_action: ValidatedAction | None = None
        self._connected = False

    # ── ActionAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        self._connected = True
        logger.info(
            "LeRobotSinkAdapter connected  joints=%s  degrees_mode=%s",
            self._joint_names,
            self._degrees_mode,
        )

    def apply(self, action: ValidatedAction) -> None:
        """Send the validated action to the robot hardware."""
        self._last_action = action
        action_dict = self._convert(action)
        self._robot.send_action(action_dict)

    def emergency_stop(self) -> None:
        logger.error("LeRobotSinkAdapter: EMERGENCY STOP triggered")
        if hasattr(self._robot, "emergency_stop"):
            self._robot.emergency_stop()

    def get_hardware_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {"connected": self._connected}
        if hasattr(self._robot, "get_state"):
            status["robot_state"] = self._robot.get_state()
        return status

    def disconnect(self) -> None:
        self._connected = False
        if self._robot is not None:
            try:
                if hasattr(self._robot, "disconnect"):
                    self._robot.disconnect()
                elif hasattr(self._robot, "close"):
                    self._robot.close()
            except Exception as e:
                logger.debug("LeRobotSinkAdapter: robot disconnect/close failed: %s", e)
        logger.info("LeRobotSinkAdapter disconnected")

    def write(self, action: ValidatedAction) -> None:
        """Deprecated alias for apply()."""
        self.apply(action)

    @property
    def last_action(self) -> ValidatedAction | None:
        return self._last_action

    # ── Internal conversion ────────────────────────────────────────────────

    def _convert(self, action: ValidatedAction) -> dict[str, Any]:
        """Build lerobot named-joint action dict from ValidatedAction.

        Joint positions are in radians (DAM internal unit).
        Revolute joints are converted to degrees; gripper passes through.
        """
        positions = np.asarray(action.target_joint_positions, dtype=np.float64)
        n = min(len(positions), len(self._joint_names))

        action_dict: dict[str, Any] = {}
        for i in range(n):
            name = self._joint_names[i]
            val = float(positions[i])
            if self._degrees_mode and not self._is_gripper_joint(name):
                val = math.degrees(val)
            action_dict[f"{name}.pos"] = val

        # gripper_action override (e.g. from a separate gripper command)
        if action.gripper_action is not None and "gripper" in self._joint_names:
            action_dict["gripper.pos"] = float(action.gripper_action)

        return action_dict

    @staticmethod
    def _is_gripper_joint(name: str) -> bool:
        return "gripper" in name.lower()
