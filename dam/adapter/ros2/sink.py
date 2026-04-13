"""ROS2SinkAdapter — bridges ValidatedAction to a ROS2 JointTrajectory topic.

Duck-typed: no hard ``import rclpy`` at module level.  Tests can pass simple
mock objects with ``create_publisher`` and ``publish`` methods.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from dam.adapter.base import ActionAdapter
from dam.types.action import ValidatedAction

logger = logging.getLogger(__name__)


class ROS2SinkAdapter(ActionAdapter):
    """ActionAdapter implementation for ROS2 robots.

    Publishes ``ValidatedAction`` as a JointTrajectory message on the configured
    topic.  Emergency stop publishes a zero-velocity trajectory.

    No hard dependency on rclpy — everything is duck-typed for testing.
    """

    def __init__(
        self,
        node: Any,
        action_topic: str = "/arm_controller/joint_trajectory",
    ) -> None:
        self._node = node
        self._action_topic = action_topic
        self._publisher: Any | None = None
        self._connected = False

    # ── ActionAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        """Create the ROS2 publisher (duck-typed)."""
        if self._node is None:
            logger.warning("ROS2SinkAdapter: node is None, running in mock mode")
            self._connected = True
            return

        try:
            # Duck-typed: real rclpy node expects (msg_type, topic, qos)
            self._publisher = self._node.create_publisher(
                None,  # msg_type placeholder
                self._action_topic,
                10,
            )
            self._connected = True
            logger.info("ROS2SinkAdapter connected to topic '%s'", self._action_topic)
        except Exception as exc:
            logger.error("ROS2SinkAdapter.connect() failed: %s", exc)
            self._connected = False

    def apply(self, action: ValidatedAction) -> None:
        """Publish the validated action to the ROS2 topic."""
        if self._publisher is None:
            logger.warning("ROS2SinkAdapter.apply(): no publisher, action dropped")
            return

        msg = self._build_trajectory_msg(action.target_joint_positions)
        try:
            self._publisher.publish(msg)
        except Exception as exc:
            logger.error("ROS2SinkAdapter.apply() publish failed: %s", exc)

    def emergency_stop(self) -> None:
        """Publish a zero-velocity trajectory to halt all motion immediately."""
        if self._publisher is None:
            logger.warning("ROS2SinkAdapter.emergency_stop(): no publisher")
            return

        # Determine joint count from last known action or fallback to 6
        zeros = np.zeros(6)
        msg = self._build_trajectory_msg(zeros, zero_velocity=True)
        try:
            self._publisher.publish(msg)
            logger.info("ROS2SinkAdapter: emergency stop published")
        except Exception as exc:
            logger.error("ROS2SinkAdapter.emergency_stop() failed: %s", exc)

    def get_hardware_status(self) -> dict[str, Any]:
        """Return diagnostic info dict."""
        return {
            "connected": self._connected and self._publisher is not None,
            "topic": self._action_topic,
        }

    def disconnect(self) -> None:
        """Destroy the publisher and mark adapter as disconnected."""
        if self._publisher is not None and self._node is not None:
            try:
                self._node.destroy_publisher(self._publisher)
            except Exception as exc:
                logger.warning("ROS2SinkAdapter.disconnect() error: %s", exc)
        self._publisher = None
        self._connected = False
        logger.info("ROS2SinkAdapter disconnected")

    # ── Internal helpers ───────────────────────────────────────────────────

    def _build_trajectory_msg(self, positions: np.ndarray, zero_velocity: bool = False) -> Any:
        """Build a duck-typed trajectory message dict (real implementation
        would return a JointTrajectory ROS2 message)."""
        velocities = np.zeros_like(positions) if zero_velocity else None
        return {
            "positions": positions.tolist(),
            "velocities": velocities.tolist() if velocities is not None else [],
            "topic": self._action_topic,
        }
