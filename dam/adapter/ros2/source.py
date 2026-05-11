"""ROS2SourceAdapter — bridges ROS2 JointState topic to DAM Observation.

rclpy / sensor_msgs / geometry_msgs are imported lazily inside ``connect()``
so this module can be imported (and unit-tested with mocks) on machines
without a ROS2 installation.  When the user passes a real ``rclpy.node.Node``,
the adapter resolves the proper message types and uses a real ``QoSProfile``.
When the node is ``None`` the adapter falls back to mock mode and logs a
clear warning — production users should always inject a node.

Observation channels
--------------------
Two flavours, matching ROS2 conventions:

1. **Topic channels** (``wrench``, …) — independent subscriptions.  Default
   topic per channel lives in ``_CHANNEL_DEFAULT_TOPICS``; stackfile may
   override the topic.
2. **JointState-derived channels** (``effort``) — read straight from the
   ``sensor_msgs/JointState`` callback, no extra subscription.
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
    """SensorAdapter for ROS2 robots reading JointState (+ optional wrench)."""

    _CHANNEL_DEFAULT_TOPICS: dict[str, str] = {
        "wrench": "/wrench",
    }
    _JOINT_STATE_DERIVED: dict[str, str] = {
        "effort": "effort",  # channel name → JointState attribute
    }

    def __init__(
        self,
        node: Any,
        joint_state_topic: str = "/joint_states",
        channel_topic_overrides: dict[str, str] | None = None,
        qos: str = "reliable",
        qos_depth: int = 10,
    ) -> None:
        """Construct the adapter.

        Parameters
        ----------
        node
            A real ``rclpy.node.Node`` (production) or a duck-typed mock with
            ``create_subscription`` / ``destroy_subscription``.  Pass ``None``
            for mock mode (zero observations, used by tests).
        joint_state_topic
            Topic carrying ``sensor_msgs/JointState``.  Defaults to
            ``/joint_states``.
        channel_topic_overrides
            Per-channel topic redirects (e.g. ``{"wrench": "/ft/wrench"}``).
        qos
            ``"reliable"`` or ``"best_effort"``.  Sensor data from real robots
            is typically best_effort; commands are reliable.
        qos_depth
            QoS history depth.  Default 10.
        """
        self._node = node
        self._joint_state_topic = joint_state_topic
        self._latest_msg: Any | None = None
        self._last_msg_time: float | None = None
        self._subscription: Any | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._qos_reliability = qos
        self._qos_depth = qos_depth

        self._channel_topic_overrides: dict[str, str] = dict(channel_topic_overrides or {})
        self._channel_topics: dict[str, str] = {}
        self._derived_channels: set[str] = set()
        self._channel_data: dict[str, np.ndarray] = {}
        self._channel_subscriptions: dict[str, Any] = {}

    # ── SensorAdapter ABC ──────────────────────────────────────────────────

    def connect(self) -> None:
        if self._node is None:
            logger.warning(
                "ROS2SourceAdapter: node is None — running in mock mode; no real "
                "subscriptions will be created.  Production callers must inject a "
                "rclpy.node.Node via RuntimeFactory.build_from_stackfile(path, "
                "ros2_node=...)"
            )
            self._connected = True
            return

        msg_types = self._resolve_msg_types()
        qos = self._build_qos()

        try:
            self._subscription = self._node.create_subscription(
                msg_types["joint_state"],
                self._joint_state_topic,
                self._on_joint_state,
                qos,
            )
            for channel, topic in self._channel_topics.items():
                msg_type = msg_types["wrench"] if channel == "wrench" else msg_types["array"]
                self._channel_subscriptions[channel] = self._node.create_subscription(
                    msg_type,
                    topic,
                    lambda msg, ch=channel: self._on_channel_msg(ch, msg),
                    qos,
                )
                logger.info("ROS2SourceAdapter channel '%s' ← topic '%s'", channel, topic)
            self._connected = True
            logger.info(
                "ROS2SourceAdapter connected to '%s' (qos=%s depth=%d)",
                self._joint_state_topic,
                self._qos_reliability,
                self._qos_depth,
            )
        except Exception:
            logger.error("ROS2SourceAdapter.connect() failed", exc_info=True)
            # Tear down any subscriptions that succeeded before the failure so
            # we don't leak channels into a half-connected state.
            self.disconnect()

    def read(self) -> Observation:
        with self._lock:
            msg = self._latest_msg
            channels = {k: v.copy() for k, v in self._channel_data.items()} or None

        if msg is None:
            return Observation(
                timestamp=time.monotonic(),
                joint_positions=np.zeros(6),
                joint_velocities=np.zeros(6),
                channels=channels,
            )
        return self._convert(msg, channels)

    def supported_channels(self) -> set[str]:
        return set(self._CHANNEL_DEFAULT_TOPICS) | set(self._JOINT_STATE_DERIVED)

    def set_observation_channels(self, channels: list[str]) -> None:
        for ch in channels:
            if ch in self._JOINT_STATE_DERIVED:
                self._derived_channels.add(ch)
            elif ch in self._CHANNEL_DEFAULT_TOPICS:
                self._channel_topics[ch] = self._channel_topic_overrides.get(
                    ch, self._CHANNEL_DEFAULT_TOPICS[ch]
                )
            else:
                logger.warning("ROS2SourceAdapter: unknown channel '%s' — ignoring", ch)

    def is_healthy(self) -> bool:
        if self._node is None or not self._connected:
            return False
        with self._lock:
            if self._last_msg_time is None:
                return False
            return (time.monotonic() - self._last_msg_time) < 1.0

    def disconnect(self) -> None:
        if self._subscription is not None and self._node is not None:
            try:
                self._node.destroy_subscription(self._subscription)
            except Exception as exc:
                logger.warning("ROS2SourceAdapter.disconnect() error: %s", exc)
        for sub in self._channel_subscriptions.values():
            try:
                self._node.destroy_subscription(sub)
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.debug("ROS2SourceAdapter channel disconnect: %s", exc)
        self._channel_subscriptions.clear()
        self._subscription = None
        self._connected = False
        logger.info("ROS2SourceAdapter disconnected")

    # ── Callbacks ──────────────────────────────────────────────────────────

    def _on_joint_state(self, msg: Any) -> None:
        derived: dict[str, np.ndarray] = {}
        for ch in self._derived_channels:
            attr = self._JOINT_STATE_DERIVED[ch]
            raw = getattr(msg, attr, None)
            if raw is None or len(raw) == 0:
                continue
            try:
                derived[ch] = np.asarray(raw, dtype=np.float64).flatten()
            except Exception:  # noqa: BLE001
                continue

        with self._lock:
            self._latest_msg = msg
            self._last_msg_time = time.monotonic()
            self._channel_data.update(derived)

    def _on_channel_msg(self, channel: str, msg: Any) -> None:
        data: Any
        if hasattr(msg, "wrench"):
            w = msg.wrench
            data = [w.force.x, w.force.y, w.force.z, w.torque.x, w.torque.y, w.torque.z]
        elif hasattr(msg, "data"):
            data = msg.data
        else:
            data = msg
        try:
            arr = np.asarray(data, dtype=np.float64).flatten()
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            self._channel_data[channel] = arr

    # ── Internal: rclpy resolution ─────────────────────────────────────────

    def _resolve_msg_types(self) -> dict[str, Any]:
        """Best-effort lazy import of std ROS2 message types.

        Returns a dict of msg classes; values fall back to ``None`` when the
        package isn't installed (mock-mode test path).
        """
        types: dict[str, Any] = {"joint_state": None, "wrench": None, "array": None}
        try:
            from sensor_msgs.msg import JointState  # type: ignore[import-not-found]

            types["joint_state"] = JointState
        except ImportError:
            logger.warning("sensor_msgs not installed — JointState subscription will fail")
        try:
            from geometry_msgs.msg import WrenchStamped  # type: ignore[import-not-found]

            types["wrench"] = WrenchStamped
        except ImportError:
            logger.debug("geometry_msgs not installed — wrench channel will fall back to None")
        try:
            from std_msgs.msg import Float64MultiArray  # type: ignore[import-not-found]

            types["array"] = Float64MultiArray
        except ImportError:
            logger.debug("std_msgs not installed — array channels will fall back to None")
        return types

    def _build_qos(self) -> Any:
        """Build a ``rclpy.qos.QoSProfile`` if rclpy is installed; else return
        the depth as a plain int (the duck-typed fallback the mocks expect)."""
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

    # ── Internal conversion ────────────────────────────────────────────────

    def _convert(self, msg: Any, channels: dict[str, np.ndarray] | None) -> Observation:
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
