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
    assert all(abs(v) < 1e-9 for v in published_msg["positions"])
    # zero_velocity flag → velocities list present and all zero
    assert "velocities" in published_msg
    assert all(abs(v) < 1e-9 for v in published_msg["velocities"])


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


# ── Observation-channel tests ──────────────────────────────────────────────


def test_source_supported_channels_is_nonempty():
    adapter = ROS2SourceAdapter(node=None)
    assert adapter.supported_channels() == {"effort", "wrench"}


def test_set_observation_channels_subscribes_each_topic():
    node = make_mock_node()
    adapter = ROS2SourceAdapter(
        node=node,
        channel_topic_overrides={"effort": "/my_effort"},
    )
    adapter.set_observation_channels(["effort", "wrench"])
    adapter.connect()

    # 1 joint-state subscription + 2 channel subscriptions
    assert node.create_subscription.call_count == 3
    subscribed_topics = {call.args[1] for call in node.create_subscription.call_args_list}
    assert "/my_effort" in subscribed_topics  # override
    assert "/wrench" in subscribed_topics  # default


def test_channel_message_appears_in_observation():
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node)
    adapter.set_observation_channels(["effort"])
    adapter.connect()

    # Feed a joint-state and a channel message
    adapter._on_joint_state(make_mock_joint_state_msg([0.0] * 6))
    effort_msg = MagicMock(spec=["data"])
    effort_msg.data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    adapter._on_channel_msg("effort", effort_msg)

    obs = adapter.read()
    assert obs.channels is not None
    assert "effort" in obs.channels
    np.testing.assert_array_equal(obs.channels["effort"], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_factory_ros2_path_activates_channels_and_preserves_source_name():
    """Stackfile-driven ROS2 build wires channels and keeps the user's source name."""
    import tempfile

    import yaml

    from dam.runtime.factory import RuntimeFactory

    stack = {
        "version": "1",
        "hardware": {
            "preset": "generic_6dof",
            "sources": {
                "my_arm": {"type": "ros2", "joint_topic": "/jpos"},
                "effort": {"type": "effort", "ref": "my_arm", "topic": "/torque"},
                "wrench": {"type": "wrench", "ref": "my_arm"},
            },
            "sinks": {"cmd": {"topic": "/cmd"}},
        },
        "safety": {"control_frequency_hz": 10, "no_task_behavior": "emergency_stop"},
        "guards": [],
        "tasks": {"default": {"description": "", "boundaries": []}},
        "boundaries": {},
    }

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.safe_dump(stack, f, sort_keys=False)
        path = f.name

    runner = RuntimeFactory.build_from_stackfile(path)
    assert type(runner).__name__ == "ROS2Runner"
    # Source registered under the stackfile name, not "ros2"
    assert "my_arm" in runner.runtime._sources
    src = runner.runtime._sources["my_arm"]
    assert src._channel_topics == {"effort": "/torque", "wrench": "/wrench"}
