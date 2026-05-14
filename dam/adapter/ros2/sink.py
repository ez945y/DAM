"""ROS2SinkAdapter — publishes ValidatedAction as trajectory_msgs/JointTrajectory.

trajectory_msgs is imported lazily inside ``connect()`` / ``apply()`` so this
module can be imported on machines without ROS2.  When a duck-typed mock
node is used (tests), the sink falls back to publishing plain dicts so
assertions on ``msg["positions"]`` still work.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.adapter.base import ActionAdapter
from dam.types.action import ValidatedAction

logger = logging.getLogger(__name__)


class ROS2SinkAdapter(ActionAdapter):
    """ActionAdapter that publishes JointTrajectory messages over ROS2."""

    def __init__(
        self,
        node: Any,
        action_topic: str = "/arm_controller/joint_trajectory",
        joint_names: list[str] | None = None,
        qos: str = "reliable",
        qos_depth: int = 10,
    ) -> None:
        """Construct the sink.

        Parameters
        ----------
        node
            rclpy.node.Node (production) or a duck-typed mock.  ``None`` →
            mock mode, no real publisher.
        action_topic
            Topic to publish JointTrajectory on.
        joint_names
            Required for real JointTrajectory publication (ROS2 controllers
            need ``joint_names`` on every command).  Empty list is accepted
            for mock-mode tests but logs a warning at connect time.
        qos
            ``"reliable"`` (default — commands should never be dropped) or
            ``"best_effort"``.
        qos_depth
            QoS history depth.
        """
        self._node = node
        self._action_topic = action_topic
        self._joint_names: list[str] = list(joint_names or [])
        self._publisher: Any | None = None
        self._connected = False
        self._qos_reliability = qos
        self._qos_depth = qos_depth
        self._traj_msg_cls: Any | None = None
        self._point_msg_cls: Any | None = None

    # ── ActionAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        if self._connected:
            return
        if self._node is None:
            logger.warning(
                "ROS2SinkAdapter: node is None — mock mode; commands will publish "
                "as plain dicts, not real JointTrajectory."
            )
            self._connected = True
            return

        if not self._joint_names:
            logger.warning(
                "ROS2SinkAdapter: joint_names is empty; published JointTrajectory "
                "messages will be missing the required joint_names field."
            )

        self._resolve_msg_types()
        qos = self._build_qos()

        try:
            self._publisher = self._node.create_publisher(
                self._traj_msg_cls,
                self._action_topic,
                qos,
            )
            self._connected = True
            logger.info(
                "ROS2SinkAdapter connected to '%s' (qos=%s depth=%d)",
                self._action_topic,
                self._qos_reliability,
                self._qos_depth,
            )
        except Exception:
            logger.error("ROS2SinkAdapter.connect() failed", exc_info=True)
            self.disconnect()

    def apply(self, action: ValidatedAction) -> None:
        if self._publisher is None:
            logger.warning("ROS2SinkAdapter.apply(): no publisher, action dropped")
            return
        # ValidatedAction.__post_init__ already coerces both arrays to float64.
        msg = self._build_trajectory_msg(
            action.target_joint_positions, action.target_joint_velocities
        )
        try:
            self._publisher.publish(msg)
        except Exception:
            logger.error("ROS2SinkAdapter.apply() publish failed", exc_info=True)

    def emergency_stop(self) -> None:
        if self._publisher is None:
            logger.warning("ROS2SinkAdapter.emergency_stop(): no publisher")
            return
        n = max(len(self._joint_names), 1)
        msg = self._build_trajectory_msg(np.zeros(n), np.zeros(n), zero_velocity=True)
        try:
            self._publisher.publish(msg)
            logger.info("ROS2SinkAdapter: emergency stop published")
        except Exception:
            logger.error("ROS2SinkAdapter.emergency_stop() failed", exc_info=True)

    def get_hardware_status(self) -> dict[str, Any]:
        return {
            "connected": self._connected and self._publisher is not None,
            "topic": self._action_topic,
            "joint_names": list(self._joint_names),
        }

    def disconnect(self) -> None:
        if not self._connected and self._publisher is None:
            return
        if self._publisher is not None and self._node is not None:
            try:
                self._node.destroy_publisher(self._publisher)
            except Exception as exc:
                logger.warning("ROS2SinkAdapter.disconnect() error: %s", exc)
        self._publisher = None
        self._connected = False
        logger.info("ROS2SinkAdapter disconnected")

    # ── Internal helpers ───────────────────────────────────────────────────

    def _resolve_msg_types(self) -> None:
        try:
            from trajectory_msgs.msg import (  # type: ignore[import-not-found]
                JointTrajectory,
                JointTrajectoryPoint,
            )

            self._traj_msg_cls = JointTrajectory
            self._point_msg_cls = JointTrajectoryPoint
        except ImportError:
            logger.warning(
                "trajectory_msgs not installed — falling back to dict messages "
                "(suitable for tests only, not for real robots)"
            )
            self._traj_msg_cls = None
            self._point_msg_cls = None

    def _build_qos(self) -> Any:
        try:
            from rclpy.qos import QoSProfile, ReliabilityPolicy
        except ImportError:
            return self._qos_depth
        reliability = (
            ReliabilityPolicy.BEST_EFFORT
            if self._qos_reliability == "best_effort"
            else ReliabilityPolicy.RELIABLE
        )
        return QoSProfile(depth=self._qos_depth, reliability=reliability)

    def _build_trajectory_msg(
        self,
        positions: np.ndarray,
        velocities: np.ndarray | None,
        zero_velocity: bool = False,
    ) -> Any:
        """Real ``JointTrajectory`` when trajectory_msgs is available; otherwise
        a dict that preserves the test-friendly mock-mode shape."""
        if self._traj_msg_cls is None or self._point_msg_cls is None:
            return {
                "positions": positions.tolist(),
                "velocities": (
                    np.zeros_like(positions).tolist()
                    if zero_velocity
                    else (velocities.tolist() if velocities is not None else [])
                ),
                "topic": self._action_topic,
            }

        msg = self._traj_msg_cls()
        msg.joint_names = list(self._joint_names)
        point = self._point_msg_cls()
        point.positions = positions.tolist()
        if zero_velocity:
            point.velocities = np.zeros_like(positions).tolist()
        elif velocities is not None:
            point.velocities = velocities.tolist()
        msg.points = [point]
        return msg
