"""ROS2SourceAdapter — bridges ROS2 JointState topic to DAM Observation.

Duck-typed: no hard ``import rclpy`` at module level.  The adapter works with
any node object that has ``create_subscription`` (real rclpy node) or a mock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from dam.adapter.base import SensorAdapter
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class ROS2SourceAdapter(SensorAdapter):
    """SensorAdapter for ROS2 robots reading JointState and tool-pose topics.

    No hard dependency on rclpy — everything is duck-typed.  Tests can pass
    simple mock objects with ``create_subscription`` and ``destroy_subscription``.

    Expected msg interface (duck-typed)
    ------------------------------------
    JointState msg: ``msg.name``, ``msg.position``, ``msg.velocity``
    Pose msg:       ``msg.pose.position``, ``msg.pose.orientation``  (optional)
    """

    def __init__(
        self,
        node: Any,
        joint_state_topic: str = "/joint_states",
        ee_topic: str = "/tool_pose",
    ) -> None:
        self._node = node
        self._joint_state_topic = joint_state_topic
        self._ee_topic = ee_topic
        self._latest_msg: Any | None = None
        self._last_msg_time: float | None = None
        self._subscription: Any | None = None
        self._lock = threading.Lock()
        self._connected = False

    # ── SensorAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        """Subscribe to the joint state topic via duck-typed node."""
        if self._node is None:
            logger.warning("ROS2SourceAdapter: node is None, running in mock mode")
            self._connected = True
            return

        try:
            # Duck-typed: works with real rclpy node or mock
            # We do NOT hard-import rclpy.sensor_msgs — caller provides msg class or None
            self._subscription = self._node.create_subscription(
                None,  # msg_type placeholder — real node expects rclpy MsgType
                self._joint_state_topic,
                self._on_joint_state,
                10,  # QoS depth
            )
            self._connected = True
            logger.info("ROS2SourceAdapter connected to topic '%s'", self._joint_state_topic)
        except Exception as exc:
            logger.error("ROS2SourceAdapter.connect() failed: %s", exc)
            self._connected = False

    def read(self) -> Observation:
        """Return the latest buffered Observation (from ``_latest_msg``)."""
        with self._lock:
            msg = self._latest_msg

        if msg is None:
            # Return a zero observation so callers don't crash
            return Observation(
                timestamp=time.monotonic(),
                joint_positions=np.zeros(6),
                joint_velocities=np.zeros(6),
            )

        return self._convert(msg)

    def is_healthy(self) -> bool:
        """Return True if node is not None and a message was received recently (< 1 s)."""
        if self._node is None or not self._connected:
            return False
        with self._lock:
            if self._last_msg_time is None:
                return False
            return (time.monotonic() - self._last_msg_time) < 1.0

    def disconnect(self) -> None:
        """Destroy the subscription and mark adapter as disconnected."""
        if self._subscription is not None and self._node is not None:
            try:
                self._node.destroy_subscription(self._subscription)
            except Exception as exc:
                logger.warning("ROS2SourceAdapter.disconnect() error: %s", exc)
        self._subscription = None
        self._connected = False
        logger.info("ROS2SourceAdapter disconnected")

    # ── Callback ──────────────────────────────────────────────────────────

    def _on_joint_state(self, msg: Any) -> None:
        """Callback invoked by ROS2 (or mock) when a new JointState arrives."""
        with self._lock:
            self._latest_msg = msg
            self._last_msg_time = time.monotonic()

    # ── Internal conversion ────────────────────────────────────────────────

    def _convert(self, msg: Any) -> Observation:
        """Convert a duck-typed JointState msg to an Observation."""
        positions = np.asarray(msg.position, dtype=np.float64).flatten()
        n = len(positions)

        velocities_raw = getattr(msg, "velocity", None)
        if velocities_raw is not None and len(velocities_raw) == n:
            velocities = np.asarray(velocities_raw, dtype=np.float64).flatten()
        else:
            velocities = np.zeros(n)

        return Observation(
            timestamp=time.monotonic(),
            joint_positions=positions,
            joint_velocities=velocities,
        )
