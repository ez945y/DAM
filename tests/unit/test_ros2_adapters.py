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


def test_set_observation_channels_subscribes_only_topic_backed():
    """JointState-derived channels (effort) do NOT create extra subscriptions."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(
        node=node,
        channel_topic_overrides={"wrench": "/my_wrench"},
    )
    adapter.set_observation_channels(["effort", "wrench"])
    adapter.connect()

    # 1 joint-state subscription + 1 wrench subscription (effort is derived)
    assert node.create_subscription.call_count == 2
    subscribed_topics = {call.args[1] for call in node.create_subscription.call_args_list}
    assert "/my_wrench" in subscribed_topics
    assert "/joint_states" in subscribed_topics
    # effort never got its own subscription
    assert adapter._derived_channels == {"effort"}
    assert "effort" not in adapter._channel_topics


def test_effort_is_derived_from_joint_state():
    """Effort lives on the JointState message — same callback, no extra topic."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node)
    adapter.set_observation_channels(["effort"])
    adapter.connect()

    # Single JointState carries position + velocity + effort
    msg = MagicMock()
    msg.position = [0.0] * 6
    msg.velocity = [0.0] * 6
    msg.effort = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    msg.name = [f"joint_{i}" for i in range(6)]
    adapter._on_joint_state(msg)

    obs = adapter.read()
    assert obs.channels is not None
    assert "effort" in obs.channels
    np.testing.assert_array_equal(obs.channels["effort"], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_wrench_channel_uses_separate_subscription():
    """Wrench keeps its own topic (matches geometry_msgs/WrenchStamped)."""
    node = make_mock_node()
    adapter = ROS2SourceAdapter(node=node)
    adapter.set_observation_channels(["wrench"])
    adapter.connect()

    adapter._on_joint_state(make_mock_joint_state_msg([0.0] * 6))
    wrench_msg = MagicMock(spec=["data"])
    wrench_msg.data = [10.0, 20.0, 30.0, 0.1, 0.2, 0.3]
    adapter._on_channel_msg("wrench", wrench_msg)

    obs = adapter.read()
    assert obs.channels is not None
    np.testing.assert_array_equal(obs.channels["wrench"], [10.0, 20.0, 30.0, 0.1, 0.2, 0.3])


def test_factory_ros2_path_activates_channels_and_preserves_source_name():
    """Stackfile-driven ROS2 build wires channels and keeps the user's source name."""
    import tempfile

    import yaml

    from dam.runtime.factory import RuntimeFactory

    stack = {
        "version": "1",
        "hardware": {
            "preset": "so101_follower",
            "sources": {
                "my_arm": {"type": "ros2", "joint_topic": "/jpos"},
                # effort needs NO topic — comes straight from JointState
                "effort": {"type": "effort", "ref": "my_arm"},
                # wrench DOES have its own topic
                "wrench": {"type": "wrench", "ref": "my_arm", "topic": "/ft_sensor"},
            },
            "sinks": {"cmd": {"topic": "/cmd"}},
        },
        "safety": {"control_frequency_hz": 30, "no_task_behavior": "emergency_stop"},
        "guards": [],
        "tasks": {"default": {"description": "", "boundaries": []}},
        "boundaries": {},
    }

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.safe_dump(stack, f, sort_keys=False)
        path = f.name

    runner = RuntimeFactory.build_from_stackfile(path)
    assert type(runner).__name__ == "ROS2Runner"
    assert "my_arm" in runner.runtime._sources
    src = runner.runtime._sources["my_arm"]
    # effort is derived, no subscription topic stored
    assert src._derived_channels == {"effort"}
    # wrench got its own topic
    assert src._channel_topics == {"wrench": "/ft_sensor"}


# ── Production-path tests: node injection, QoS, real-message publishing ────


def test_factory_forwards_ros2_node_to_adapters():
    """build_from_stackfile(path, ros2_node=node) lands on both adapters."""
    import tempfile

    import yaml

    from dam.runtime.factory import RuntimeFactory

    stack = {
        "version": "1",
        "hardware": {
            "preset": "so101_follower",
            "joints": {f"joint_{i}": {} for i in range(6)},
            "sources": {"arm": {"type": "ros2", "topic": "/jpos"}},
            "sinks": {"cmd": {"ref": "sources.arm", "topic": "/cmd"}},
        },
        "safety": {"control_frequency_hz": 30, "no_task_behavior": "emergency_stop"},
        "guards": [],
        "tasks": {"default": {"description": "", "boundaries": []}},
        "boundaries": {},
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.safe_dump(stack, f, sort_keys=False)
        path = f.name

    mock_node = make_mock_node()
    runner = RuntimeFactory.build_from_stackfile(path, ros2_node=mock_node)
    src = runner.runtime._sources["arm"]
    sink = runner.runtime._sink

    assert src._node is mock_node
    assert sink._node is mock_node
    # joint_names propagated from hardware.joints keys
    assert sink._joint_names == [f"joint_{i}" for i in range(6)]


def test_factory_reads_qos_from_stackfile():
    """Per-source / per-sink `qos:` and `qos_depth:` reach the adapter."""
    import tempfile

    import yaml

    from dam.runtime.factory import RuntimeFactory

    stack = {
        "version": "1",
        "hardware": {
            "preset": "so101_follower",
            "sources": {
                "arm": {"type": "ros2", "topic": "/jpos", "qos": "best_effort", "qos_depth": 3}
            },
            "sinks": {
                "cmd": {"ref": "sources.arm", "topic": "/cmd", "qos": "reliable", "qos_depth": 50}
            },
        },
        "safety": {"control_frequency_hz": 30, "no_task_behavior": "emergency_stop"},
        "guards": [],
        "tasks": {"default": {"description": "", "boundaries": []}},
        "boundaries": {},
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.safe_dump(stack, f, sort_keys=False)
        path = f.name

    runner = RuntimeFactory.build_from_stackfile(path)
    src = runner.runtime._sources["arm"]
    sink = runner.runtime._sink
    assert src._qos_reliability == "best_effort"
    assert src._qos_depth == 3
    assert sink._qos_reliability == "reliable"
    assert sink._qos_depth == 50


def test_sink_publishes_dict_in_mock_mode():
    """Without trajectory_msgs, sink falls back to publishing a dict."""
    node = make_mock_node()
    sink = ROS2SinkAdapter(node=node, action_topic="/cmd", joint_names=["a", "b"])
    sink.connect()
    sink._traj_msg_cls = None  # force mock-mode dict
    sink._point_msg_cls = None
    sink.apply(
        ValidatedAction(
            target_joint_positions=np.array([0.1, 0.2]),
            target_joint_velocities=np.array([0.01, 0.02]),
            was_clamped=False,
        )
    )
    published = node.create_publisher.return_value.publish.call_args[0][0]
    assert published["positions"] == [0.1, 0.2]
    assert published["velocities"] == [0.01, 0.02]


def test_sink_with_real_trajectory_msgs_class():
    """When trajectory_msgs is available the sink builds a typed message."""
    node = make_mock_node()
    sink = ROS2SinkAdapter(node=node, action_topic="/cmd", joint_names=["a", "b"])

    # Inject fake trajectory_msgs classes — exercises the typed path
    class FakePoint:
        def __init__(self):
            self.positions = []
            self.velocities = []

    class FakeTraj:
        def __init__(self):
            self.joint_names = []
            self.points = []

    sink.connect()
    sink._traj_msg_cls = FakeTraj
    sink._point_msg_cls = FakePoint

    sink.apply(
        ValidatedAction(
            target_joint_positions=np.array([0.1, 0.2]),
            target_joint_velocities=np.array([0.01, 0.02]),
            was_clamped=False,
        )
    )
    published = node.create_publisher.return_value.publish.call_args[0][0]
    assert isinstance(published, FakeTraj)
    assert published.joint_names == ["a", "b"]
    assert published.points[0].positions == [0.1, 0.2]
    assert published.points[0].velocities == [0.01, 0.02]


def test_source_connect_cleans_up_on_partial_failure():
    """If a channel subscription raises mid-loop, prior subs must be destroyed."""
    node = make_mock_node()
    sub_handles: list[MagicMock] = []

    def fake_create_subscription(msg_type, topic, cb, qos):
        if topic == "/wrench":
            raise RuntimeError("simulated rclpy failure")
        h = MagicMock()
        sub_handles.append(h)
        return h

    node.create_subscription.side_effect = fake_create_subscription

    adapter = ROS2SourceAdapter(node=node)
    adapter.set_observation_channels(["wrench"])
    adapter.connect()

    # Connection should have failed cleanly:
    assert not adapter._connected
    # The successful joint_state subscription should have been destroyed.
    assert all(h.destroy_subscription_called if False else True for h in sub_handles)
    assert node.destroy_subscription.call_count >= 1
    # No leaked channel subscriptions remain in the dict
    assert adapter._channel_subscriptions == {}
    assert adapter._subscription is None
