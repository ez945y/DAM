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
    Channel msgs:   any iterable of floats via ``msg.data`` (Float64MultiArray)
                    or ``msg.wrench.force/torque`` (WrenchStamped, for ``wrench``)
    """

    # Channel-name → default topic.  Override per-channel via stackfile.
    _CHANNEL_DEFAULT_TOPICS: dict[str, str] = {
        "effort": "/joint_effort",
        "wrench": "/wrench",
        "temperature": "/joint_temperature",
        "current": "/joint_current",
        "voltage": "/joint_voltage",
    }

    def __init__(
        self,
        node: Any,
        joint_state_topic: str = "/joint_states",
        ee_topic: str = "/tool_pose",
        channel_topic_overrides: dict[str, str] | None = None,
    ) -> None:
        self._node = node
        self._joint_state_topic = joint_state_topic
        self._ee_topic = ee_topic
        self._latest_msg: Any | None = None
        self._last_msg_time: float | None = None
        self._subscription: Any | None = None
        self._lock = threading.Lock()
        self._connected = False

        # Channel state: name → (topic, np.ndarray | None)
        self._channel_topic_overrides: dict[str, str] = dict(channel_topic_overrides or {})
        self._channel_topics: dict[str, str] = {}
        self._channel_data: dict[str, np.ndarray] = {}
        self._channel_subscriptions: dict[str, Any] = {}

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
            for channel, topic in self._channel_topics.items():
                self._channel_subscriptions[channel] = self._node.create_subscription(
                    None,
                    topic,
                    lambda msg, ch=channel: self._on_channel_msg(ch, msg),
                    10,
                )
                logger.info("ROS2SourceAdapter channel '%s' ← topic '%s'", channel, topic)
            self._connected = True
            logger.info("ROS2SourceAdapter connected to topic '%s'", self._joint_state_topic)
        except Exception as exc:
            logger.error("ROS2SourceAdapter.connect() failed: %s", exc)
            self._connected = False

    def read(self) -> Observation:
        """Return the latest buffered Observation (from ``_latest_msg``)."""
        with self._lock:
            msg = self._latest_msg
            channels = {k: v.copy() for k, v in self._channel_data.items()} or None

        if msg is None:
            # Return a zero observation so callers don't crash
            return Observation(
                timestamp=time.monotonic(),
                joint_positions=np.zeros(6),
                joint_velocities=np.zeros(6),
                channels=channels,
            )

        return self._convert(msg, channels)

    def supported_channels(self) -> set[str]:
        return set(self._CHANNEL_DEFAULT_TOPICS)

    def set_observation_channels(self, channels: list[str]) -> None:
        """Activate the given channels.  Resolve each to a topic via the
        per-channel override map passed at construction, falling back to the
        adapter's default topic for that channel."""
        for ch in channels:
            self._channel_topics[ch] = self._channel_topic_overrides.get(
                ch, self._CHANNEL_DEFAULT_TOPICS[ch]
            )

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
        for sub in self._channel_subscriptions.values():
            try:
                self._node.destroy_subscription(sub)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.debug("ROS2SourceAdapter channel disconnect: %s", exc)
        self._channel_subscriptions.clear()
        self._subscription = None
        self._connected = False
        logger.info("ROS2SourceAdapter disconnected")

    # ── Callback ──────────────────────────────────────────────────────────

    def _on_joint_state(self, msg: Any) -> None:
        """Callback invoked by ROS2 (or mock) when a new JointState arrives."""
        with self._lock:
            self._latest_msg = msg
            self._last_msg_time = time.monotonic()

    def _on_channel_msg(self, channel: str, msg: Any) -> None:
        """Callback for an observation-channel topic.  Duck-typed extraction."""
        data: Any
        if hasattr(msg, "data"):
            data = msg.data
        elif hasattr(msg, "wrench"):
            w = msg.wrench
            data = [w.force.x, w.force.y, w.force.z, w.torque.x, w.torque.y, w.torque.z]
        else:
            data = msg
        try:
            arr = np.asarray(data, dtype=np.float64).flatten()
        except Exception:  # noqa: BLE001 — unparseable message
            return
        with self._lock:
            self._channel_data[channel] = arr

    # ── Internal conversion ────────────────────────────────────────────────

    def _convert(self, msg: Any, channels: dict[str, np.ndarray] | None) -> Observation:
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
            channels=channels,
        )
