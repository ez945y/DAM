from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from dam.adapter.dataset import DatasetReplayPolicy, DatasetSimSource
from dam.config.schema import NodeConfig, StackfileConfig
from dam.runtime.factory import RuntimeFactory
from dam.runtime.guard_runtime import GuardRuntime
from dam.types.action import ActionProposal
from dam.types.observation import Observation


def test_dataset_replay_policy_emits_action_aligned_with_latest_observation(monkeypatch):
    frames = [
        {
            "joint_positions": np.array([10.0, 20.0]),
            "action": np.array([30.0, 40.0]),
            "images": {"top": np.zeros((2, 2, 3), dtype=np.uint8)},
        }
    ]
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _repo, _ep: (frames, 30.0))
    )
    source = DatasetSimSource("demo/repo", degrees_mode=True, strict=True)
    source.connect()

    obs = source.read()
    proposal = DatasetReplayPolicy(source).predict(obs)

    np.testing.assert_allclose(obs.joint_positions, np.radians([10.0, 20.0]))
    np.testing.assert_allclose(proposal.target_joint_positions, np.radians([30.0, 40.0]))
    assert proposal.policy_name == "dataset_replay"
    assert set(obs.images or {}) == {"top"}


def test_dataset_hardware_replay_requires_recorded_actions(monkeypatch):
    monkeypatch.setattr(
        DatasetSimSource,
        "_load_episode",
        staticmethod(lambda _repo, _ep: ([{"joint_positions": np.zeros(2)}], 30.0)),
    )
    with np.testing.assert_raises_regex(ValueError, "requires.*action"):
        DatasetSimSource("demo/repo", strict=True)


def _hybrid_config() -> StackfileConfig:
    return StackfileConfig(
        **{
            "version": "1",
            "hardware": {
                "preset": "so101_follower",
                "sources": {
                    "replay": {"type": "dataset", "dataset_repo_id": "demo/repo"},
                    "arm": {"type": "motor"},
                    "current": {"type": "current", "ref": "arm"},
                },
                "sinks": {"command": {"ref": "sources.arm"}},
            },
            "guards": [],
            "tasks": {"default": {"boundaries": []}},
            "boundaries": {},
        }
    )


def test_factory_routes_dataset_plus_motor_to_hardware_sink(monkeypatch):
    from dam.adapter.lerobot.adapter import LeRobotAdapter
    from dam.adapter.lerobot.builder import LeRobotBuilder
    from dam.runner.lerobot import LeRobotRunner

    source = SimpleNamespace()
    fake_robot = object()
    fake_preset = SimpleNamespace(degrees_mode=True, default_urdf_relpath=None)
    monkeypatch.setattr(RuntimeFactory, "_build_sim_source", staticmethod(lambda *a, **k: source))
    monkeypatch.setattr(LeRobotBuilder, "build_robot", lambda _self: fake_robot)
    monkeypatch.setattr(LeRobotBuilder, "joint_names", ["a", "b"], raising=False)
    monkeypatch.setattr(LeRobotBuilder, "preset", fake_preset, raising=False)

    runner = RuntimeFactory.build_from_config(_hybrid_config())

    assert isinstance(runner, LeRobotRunner)
    assert runner.runtime._sources["replay"] is source
    motor = runner.runtime._sources["arm"]
    assert isinstance(motor, LeRobotAdapter)
    assert runner.runtime._sink is motor
    assert isinstance(runner.runtime._policy, DatasetReplayPolicy)
    assert motor._observation_channels == ["current"]


def test_secondary_hardware_source_channels_are_visible_to_policy():
    config = StackfileConfig(
        **{
            "guards": [],
            "tasks": {"default": {"boundaries": []}},
            "boundaries": {},
        }
    )
    runtime = GuardRuntime._from_config(config)

    class Source:
        def read(self):
            return Observation(timestamp=1.0, joint_positions=np.zeros(2))

    class Telemetry:
        def read(self):
            return Observation(
                timestamp=1.0,
                joint_positions=np.ones(2),
                channels={"current": np.array([0.1, 0.2])},
            )

    class Policy:
        seen = None

        def predict(self, obs):
            self.seen = obs
            return ActionProposal(target_joint_positions=np.zeros(2))

    class Sink:
        def apply(self, _action):
            pass

    policy = Policy()
    runtime.register_source("replay", Source())
    runtime.register_source("arm", Telemetry())
    runtime.register_policy(policy)
    runtime.register_sink(Sink())
    runtime.start_task("default")

    runtime.step()

    np.testing.assert_allclose(policy.seen.joint_positions, np.zeros(2))
    np.testing.assert_allclose(policy.seen.channels["current"], [0.1, 0.2])


def test_legacy_gripper_phase_timeout_is_ignored_at_runtime():
    config = StackfileConfig(
        **{"guards": [], "tasks": {"default": {"boundaries": []}}, "boundaries": {}}
    )
    node = GuardRuntime._build_boundary_node(
        NodeConfig(callback="task_gripper_command_guard", timeout_sec=1.0),
        "execution",
        config,
    )
    assert node.timeout_sec is None
