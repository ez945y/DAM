"""Replay fidelity tests — verify guards check correct positions during dataset replay.

Two bugs being tested:

1. Multi-source observation merge keeps joint_positions from the dataset
   source, not the motor source. Guards (workspace, velocity) therefore
   evaluate the *intended* trajectory, not where the robot *actually is*.

2. When velocity_limit CLAMPs (e.g. 3x ratio), the robot can only move 1/3
   of commanded distance per cycle. This causes the robot to lag behind the
   dataset trajectory, yet guards see the dataset trajectory and report PASS.

These tests don't require a real robot or dataset — they use synthetic frames
to isolate each concern.
"""

from __future__ import annotations

import numpy as np
import pytest

from dam.adapter.dataset import DatasetReplayPolicy, DatasetSimSource
from dam.boundary.callbacks._helpers import _resolve_ee_translation
from dam.config.schema import StackfileConfig
from dam.runtime.guard_runtime import GuardRuntime
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture(autouse=True)
def _register():
    from dam.boundary.builtin_callbacks import register_all as reg_callbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_guards()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_frames(positions_deg: list[list[float]], actions_deg: list[list[float]] | None = None):
    """Build synthetic dataset frames in degrees."""
    frames = []
    for i, pos in enumerate(positions_deg):
        f: dict = {"joint_positions": np.array(pos, dtype=float)}
        if actions_deg is not None:
            f["action"] = np.array(actions_deg[i], dtype=float)
        else:
            f["action"] = np.array(pos, dtype=float)
        frames.append(f)
    return frames


class StubMotorSource:
    """Simulates a motor source that reports the actual robot position."""

    def __init__(self, positions_rad: list[np.ndarray]):
        self._positions = positions_rad
        self._idx = 0

    def read(self) -> Observation:
        pos = self._positions[min(self._idx, len(self._positions) - 1)]
        self._idx += 1
        return Observation(
            timestamp=1.0,
            joint_positions=pos.copy(),
            channels={"current": np.array([0.1] * len(pos))},
        )


class StubSink:
    applied: list[np.ndarray]

    def __init__(self):
        self.applied = []

    def apply(self, action):
        self.applied.append(np.asarray(action.target_joint_positions).copy())


# ── Test 1: Multi-source merge uses dataset positions, not motor ─────────


def test_multisource_merge_keeps_dataset_positions_and_exposes_motor(monkeypatch):
    """After multi-source merge:
    - obs.joint_positions = dataset (first source, for policy)
    - obs.channels includes motor observation channels
    """
    config = StackfileConfig(
        **{"guards": [], "tasks": {"default": {"boundaries": []}}, "boundaries": {}}
    )
    runtime = GuardRuntime._from_config(config)

    dataset_pos = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.0])
    motor_pos = np.array([0.05, 0.1, 0.15, 0.2, 0.25, 0.0])

    class DatasetSource:
        def read(self):
            return Observation(timestamp=1.0, joint_positions=dataset_pos.copy())

    class MotorSource:
        def read(self):
            return Observation(
                timestamp=1.0,
                joint_positions=motor_pos.copy(),
                channels={"current": np.array([0.1] * 6)},
            )

    seen_obs = []

    class SpyPolicy:
        def predict(self, obs):
            seen_obs.append(obs)
            return ActionProposal(target_joint_positions=dataset_pos.copy())

    runtime.register_source("replay", DatasetSource())
    runtime.register_source("arm", MotorSource())
    runtime.register_policy(SpyPolicy())
    runtime.register_sink(StubSink())
    runtime.start_task("default")

    runtime.step()

    assert seen_obs, "step() did not record an observation"
    # Dataset positions are the primary observation (for policy)
    np.testing.assert_allclose(seen_obs[0].joint_positions, dataset_pos)
    # Motor channels are merged correctly
    assert "current" in (seen_obs[0].channels or {})


# ── Test 2: Velocity profile from dataset frames ────────────────────────


def test_dataset_source_velocity_between_frames(monkeypatch):
    """Verify that inter-frame velocity is computed correctly from dataset.

    For a dataset recorded at 30fps replayed at 30Hz, the implied velocity
    between consecutive frames should be: (pos[i+1] - pos[i]) * hz.
    """
    pos_deg = [
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [20.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ]
    frames = _make_frames(pos_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    source = DatasetSimSource("test/repo", hz=30.0, degrees_mode=True, strict=True)
    source.connect()

    source.read()  # frame 0
    obs1 = source.read()

    # Frame 0→1: delta = 10° = 0.1745 rad, dt = 1/30s
    expected_vel_j1 = np.radians(10.0) * 30.0  # ~5.24 rad/s
    actual_vel_j1 = obs1.joint_velocities[0]
    np.testing.assert_allclose(actual_vel_j1, expected_vel_j1, atol=0.01)


def test_dataset_velocity_exceeds_guard_limit(monkeypatch):
    """When dataset frame deltas imply velocities > guard limit, the guard
    should CLAMP. This test verifies that velocity clamping engages correctly.
    """
    # 10° per frame at 30Hz = 5.24 rad/s — exceeds 1.5 rad/s limit
    pos_deg = [
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ]
    actions_deg = [
        [10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [20.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ]
    frames = _make_frames(pos_deg, actions_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    source = DatasetSimSource("test/repo", hz=30.0, degrees_mode=True, strict=True)
    source.connect()

    config = StackfileConfig(
        **{
            "version": "1",
            "guards": [{"L1": "motion", "phase": 0}],
            "boundaries": {
                "joint_velocity_limit": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {
                            "callback": "joint_velocity_limit",
                            "params": {"max_velocities": [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]},
                        }
                    ],
                },
            },
            "tasks": {"default": {"boundaries": ["joint_velocity_limit"]}},
            "safety": {"control_frequency_hz": 30.0, "enforcement_mode": "enforce"},
        }
    )

    runtime = GuardRuntime._from_config(config)
    sink = StubSink()
    policy = DatasetReplayPolicy(source)

    runtime.register_source("replay", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)
    runtime.start_task("default")

    # First step: frame 0, velocity=0 → PASS
    runtime.step()

    # Second step: frame 1, velocity=5.24 rad/s > 1.5 rad/s → CLAMP
    result1 = runtime.step()

    assert result1.was_clamped, "velocity exceeds limit, should be clamped"

    # The clamped action should be less than the full commanded target (20°)
    if sink.applied:
        actual_j1 = sink.applied[-1][0]
        commanded_j1 = np.radians(20.0)  # action from frame 1
        assert actual_j1 < commanded_j1, (
            f"clamped position {actual_j1:.4f} should be less than "
            f"commanded {commanded_j1:.4f} due to velocity clamping"
        )


# ── Test 3: Workspace guard uses obs.joint_positions for FK ──────────────


def test_workspace_guard_evaluates_obs_joint_positions():
    """Workspace callback uses obs.joint_positions for FK.

    This test verifies the FK resolution path: _resolve_ee_translation reads
    obs.joint_positions (or obs.end_effector_pose if available).

    When obs.joint_positions = dataset values, the guard checks the dataset
    trajectory, NOT where the robot actually is.
    """
    # With a pre-computed EE pose in the observation
    obs_inside = Observation(
        timestamp=1.0,
        joint_positions=np.zeros(6),
        end_effector_pose=np.array([0.1, 0.1, 0.1, 0.0, 0.0, 0.0, 1.0]),
    )
    ee = _resolve_ee_translation(obs_inside)
    np.testing.assert_allclose(ee, [0.1, 0.1, 0.1])

    # When robot is actually at a different position but obs says it's fine
    obs_dataset = Observation(
        timestamp=1.0,
        joint_positions=np.zeros(6),
        end_effector_pose=np.array([0.1, 0.1, 0.1, 0.0, 0.0, 0.0, 1.0]),
    )
    obs_actual_robot = Observation(
        timestamp=1.0,
        joint_positions=np.ones(6) * 0.5,
        end_effector_pose=np.array([0.1, 0.1, -0.05, 0.0, 0.0, 0.0, 1.0]),
    )

    ee_dataset = _resolve_ee_translation(obs_dataset)
    ee_actual = _resolve_ee_translation(obs_actual_robot)

    # Dataset says z=0.1 (above z_min=0.02), actual robot says z=-0.05 (below)
    assert ee_dataset[2] > 0.02, "dataset trajectory shows safe position"
    assert ee_actual[2] < 0.02, "actual robot is in violation"


# ── Test 4: Cumulative trajectory error from velocity clamping ───────────


def test_velocity_clamping_causes_trajectory_lag(monkeypatch):
    """When velocity is clamped every cycle, the validated action's cumulative
    position diverges from the dataset's intended trajectory.

    After N cycles of consistent clamping (ratio ~3x), the robot position
    trails the dataset trajectory by approximately (1 - 1/ratio) * total_delta.
    """
    # 5 frames, each 10° apart → constant velocity = 5.24 rad/s at 30Hz
    n_frames = 5
    pos_deg = [[i * 10.0, 0, 0, 0, 0, 0] for i in range(n_frames)]
    actions_deg = [[(i + 1) * 10.0, 0, 0, 0, 0, 0] for i in range(n_frames)]
    frames = _make_frames(pos_deg, actions_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    source = DatasetSimSource("test/repo", hz=30.0, degrees_mode=True, strict=True)
    source.connect()

    config = StackfileConfig(
        **{
            "version": "1",
            "guards": [{"L1": "motion", "phase": 0}],
            "boundaries": {
                "joint_velocity_limit": {
                    "layer": "L1",
                    "type": "single",
                    "nodes": [
                        {
                            "callback": "joint_velocity_limit",
                            "params": {"max_velocities": [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]},
                        }
                    ],
                },
            },
            "tasks": {"default": {"boundaries": ["joint_velocity_limit"]}},
            "safety": {"control_frequency_hz": 30.0, "enforcement_mode": "enforce"},
        }
    )

    runtime = GuardRuntime._from_config(config)
    sink = StubSink()
    policy = DatasetReplayPolicy(source)

    runtime.register_source("replay", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)
    runtime.start_task("default")

    clamp_count = 0
    for _ in range(n_frames):
        result = runtime.step()
        if result.was_clamped:
            clamp_count += 1

    # Most frames should be clamped (first frame has zero velocity → PASS)
    assert clamp_count >= n_frames - 1, (
        f"expected at least {n_frames - 1} clamped cycles, got {clamp_count}"
    )

    # The last sent position should trail significantly behind the dataset target
    if len(sink.applied) >= n_frames:
        dataset_final_j1 = np.radians((n_frames) * 10.0)
        actual_final_j1 = sink.applied[-1][0]
        lag = dataset_final_j1 - actual_final_j1
        assert lag > 0, "robot should lag behind dataset trajectory"
        total_travel = dataset_final_j1
        lag_fraction = lag / total_travel if total_travel > 0 else 0
        assert lag_fraction > 0.05, (
            f"trajectory lag fraction {lag_fraction:.2f} should be > 5% "
            f"when velocity is clamped every cycle"
        )


# ── Test 5: Step-driven cursor produces correct frame sequence ────────────


def test_step_driven_cursor_at_matching_fps(monkeypatch):
    """When source_fps == runtime_hz, cursor advances exactly 1 frame per step."""
    pos_deg = [[i * 5.0, 0, 0, 0, 0, 0] for i in range(10)]
    frames = _make_frames(pos_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    source = DatasetSimSource("test/repo", hz=30.0, degrees_mode=True, strict=True)
    source.connect()

    positions = []
    for _ in range(10):
        obs = source.read()
        positions.append(obs.joint_positions[0])

    expected = [np.radians(i * 5.0) for i in range(10)]
    np.testing.assert_allclose(positions, expected, atol=1e-10)


def test_step_driven_cursor_at_mismatched_fps(monkeypatch):
    """When source_fps != runtime_hz, cursor skips/interpolates correctly.

    Example: 30fps dataset replayed at 15Hz → cursor advances by 2 frames
    per step, effectively skipping every other frame.
    """
    pos_deg = [[i * 5.0, 0, 0, 0, 0, 0] for i in range(10)]
    frames = _make_frames(pos_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    source = DatasetSimSource("test/repo", hz=15.0, degrees_mode=True, strict=True)
    source.connect()

    positions = []
    for _ in range(5):
        obs = source.read()
        positions.append(obs.joint_positions[0])

    # At hz=15, cursor advances by 30/15 = 2 per call → frames 0, 2, 4, 6, 8
    expected = [np.radians(i * 2 * 5.0) for i in range(5)]
    np.testing.assert_allclose(positions, expected, atol=1e-10)


def test_step_driven_velocity_scales_with_runtime_hz(monkeypatch):
    """Velocity should be (pos_delta) / (1/hz), not (pos_delta) / (1/source_fps).

    When hz=15 and source_fps=30, we skip every other frame, so the position
    delta is 2x larger. But dt = 1/15, so velocity = 2*delta * 15 = delta * 30.
    This is the same velocity as hz=30 — the dataset's intrinsic speed.
    """
    pos_deg = [[i * 10.0, 0, 0, 0, 0, 0] for i in range(10)]
    frames = _make_frames(pos_deg)
    monkeypatch.setattr(
        DatasetSimSource, "_load_episode", staticmethod(lambda _r, _e: (frames, 30.0))
    )

    # At hz=30: delta=10°, dt=1/30 → v = 10°*30 = 300°/s
    source_30 = DatasetSimSource("test/repo", hz=30.0, degrees_mode=True, strict=True)
    source_30.connect()
    source_30.read()
    obs_30 = source_30.read()

    # At hz=15: delta=20° (skips frame), dt=1/15 → v = 20°*15 = 300°/s
    source_15 = DatasetSimSource("test/repo", hz=15.0, degrees_mode=True, strict=True)
    source_15.connect()
    source_15.read()
    obs_15 = source_15.read()

    # Both should produce the same velocity (the dataset's intrinsic speed)
    np.testing.assert_allclose(obs_30.joint_velocities[0], obs_15.joint_velocities[0], atol=0.01)
