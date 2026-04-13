"""Unit tests for ROS2SourceAdapter and ROS2SinkAdapter.

All tests use mocks — no rclpy dependency required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from dam.adapter.ros2.sink import ROS2SinkAdapter
from dam.adapter.ros2.source import ROS2SourceAdapter
from dam.types.action import ValidatedAction

# ── Helpers ────────────────────────────────────────────────────────────────


def make_mock_node():
    """Return a mock ROS2 node with create/destroy subscription/publisher."""
    node = MagicMock()
    node.create_subscription.return_value = MagicMock()
    node.create_publisher.return_value = MagicMock()
    return node


def make_mock_joint_state_msg(positions, velocities=None):
    msg = MagicMock()
    msg.position = list(positions)
    msg.velocity = list(velocities) if velocities is not None else []
    msg.name = [f"joint_{i}" for i in range(len(positions))]
    return msg


# ── Source tests ───────────────────────────────────────────────────────────


def test_source_read_with_mock_node():
    """create_subscription is called; read() returns a valid Observation."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node, joint_state_topic="/joint_states")
    adapter.connect()

    # Verify subscription was created
    assert node.create_subscription.called

    # Simulate a message arriving via the callback
    msg = make_mock_joint_state_msg([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    adapter._on_joint_state(msg)

    obs = adapter.read()
    assert obs is not None
    np.testing.assert_allclose(obs.joint_positions, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])


def test_source_healthy_after_msg():
    """is_healthy() returns True after a message arrives within 1 second."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node)
    adapter.connect()

    # Before any message: not healthy (no msg received yet)
    assert not adapter.is_healthy()

    # Simulate message arrival
    msg = make_mock_joint_state_msg([0.0] * 6)
    adapter._on_joint_state(msg)

    assert adapter.is_healthy()


def test_source_read_returns_zero_obs_before_first_msg():
    """read() returns zero Observation before any message has been received."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node)
    adapter.connect()

    obs = adapter.read()
    assert obs is not None
    np.testing.assert_allclose(obs.joint_positions, np.zeros(6))


# ── Sink tests ─────────────────────────────────────────────────────────────


def test_sink_apply_calls_publish():
    """apply() calls publisher.publish() with the action positions."""
    node = make_mock_node()
    publisher_mock = MagicMock()
    node.create_publisher.return_value = publisher_mock

    adapter = ROS2SinkAdapter(node=node, action_topic="/arm_controller/joint_trajectory")
    adapter.connect()

    action = ValidatedAction(
        target_joint_positions=np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        was_clamped=False,
    )
    adapter.apply(action)

    assert publisher_mock.publish.called
    published_msg = publisher_mock.publish.call_args[0][0]
    assert "positions" in published_msg
    assert published_msg["positions"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_sink_emergency_stop():
    """emergency_stop() calls publisher.publish() with zeros."""
    node = make_mock_node()
    publisher_mock = MagicMock()
    node.create_publisher.return_value = publisher_mock

    adapter = ROS2SinkAdapter(node=node)
    adapter.connect()

    adapter.emergency_stop()

    assert publisher_mock.publish.called
    published_msg = publisher_mock.publish.call_args[0][0]
    assert "positions" in published_msg
    assert all(v == 0.0 for v in published_msg["positions"])
    # zero_velocity flag → velocities list present and all zero
    assert "velocities" in published_msg
    assert all(v == 0.0 for v in published_msg["velocities"])


def test_sink_hardware_status():
    """get_hardware_status() returns dict with 'connected' and 'topic' keys."""
    node = make_mock_node()
    adapter = ROS2SinkAdapter(node=node, action_topic="/my_topic")
    adapter.connect()

    status = adapter.get_hardware_status()
    assert isinstance(status, dict)
    assert "connected" in status
    assert "topic" in status
    assert status["topic"] == "/my_topic"
    assert status["connected"] is True
