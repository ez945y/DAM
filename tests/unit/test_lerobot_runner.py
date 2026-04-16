"""Unit tests for LeRobotRunner construction paths.

Covers:
  - Manual construction (supply all adapters)
  - from_stackfile() with pre-built robot/policy objects (mock)
  - from_stackfile_auto() with missing hardware section raises ValueError
  - start_task → step → stop lifecycle
  - run(n_cycles=N) returns exactly N CycleResults
  - stop() handles missing robot_ref gracefully
  - LeRobotRunner delegates step() to GuardRuntime
"""

import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.fallback.builtin import EmergencyStop, HoldPosition
from dam.fallback.chain import build_escalation_chain
from dam.fallback.registry import FallbackRegistry
from dam.guard.builtin.motion import MotionGuard
from dam.runner.lerobot import LeRobotRunner
from dam.runtime.guard_runtime import GuardRuntime
from dam.types.risk import CycleResult

# ── Shared mock objects ───────────────────────────────────────────────────────


class MockRobot:
    def __init__(self):
        self.last_action = None

    def capture_observation(self):
        return {"observation.state": [0.0] * 6}

    def send_action(self, d):
        self.last_action = d


class MockPolicy:
    def select_action(self, obs_dict):
        return np.zeros(6)


def make_runtime():
    """Build a minimal GuardRuntime with MotionGuard for testing."""
    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    build_escalation_chain(reg)
    node = BoundaryNode("n0", BoundaryConstraint(), fallback="hold_position")
    container = SingleNodeContainer(node)
    return GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"pick_and_place": ["main"]},
        config_pool={"upper": np.full(6, 5.0), "lower": np.full(6, -5.0)},
    )


def make_runner() -> LeRobotRunner:
    """Helper: fully wired mock runner."""
    from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
    from dam.adapter.lerobot.sink import LeRobotSinkAdapter
    from dam.adapter.lerobot.source import LeRobotSourceAdapter

    runtime = make_runtime()
    robot = MockRobot()

    source = LeRobotSourceAdapter(robot)
    sink = LeRobotSinkAdapter(robot)
    policy = LeRobotPolicyAdapter(MockPolicy())

    runtime.register_source("arm", source)
    runtime.register_sink(sink)
    runtime.register_policy(policy)

    return LeRobotRunner(runtime=runtime, robot=robot)


# ── Basic lifecycle ───────────────────────────────────────────────────────────


def test_runner_step_returns_cycle_result():
    runner = make_runner()
    runner.start_task("pick_and_place")
    result = runner.step()
    assert isinstance(result, CycleResult)
    runner.stop()


def test_runner_run_n_cycles_returns_exactly_n():
    runner = make_runner()
    results = runner.run("pick_and_place", n_cycles=5)
    assert len(results) == 5
    assert all(isinstance(r, CycleResult) for r in results)


def test_runner_stop_idempotent():
    """Calling stop() twice must not raise."""
    runner = make_runner()
    runner.start_task("pick_and_place")
    runner.stop()
    runner.stop()  # second stop must be safe


def test_runner_stop_without_start():
    """stop() before start_task must not raise."""
    runner = make_runner()
    runner.stop()  # no task started


# ── Control frequency ─────────────────────────────────────────────────────────


def test_runner_custom_frequency():
    from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
    from dam.adapter.lerobot.sink import LeRobotSinkAdapter
    from dam.adapter.lerobot.source import LeRobotSourceAdapter

    runtime = make_runtime()
    robot = MockRobot()

    source = LeRobotSourceAdapter(robot)
    sink = LeRobotSinkAdapter(robot)
    policy = LeRobotPolicyAdapter(MockPolicy())

    runtime.register_source("arm", source)
    runtime.register_sink(sink)
    runtime.register_policy(policy)

    runner = LeRobotRunner(
        runtime=runtime,
        robot=robot,
        control_frequency_hz=100.0,
    )
    assert runner._control_frequency_hz == 100.0
    assert abs(runner._period_sec - 0.01) < 1e-9


# ── from_stackfile_auto error handling ───────────────────────────────────────


def test_from_stackfile_auto_raises_without_hardware(tmp_path):
    """from_stackfile_auto must raise ValueError if no hardware section."""
    stackfile = tmp_path / "no_hw.yaml"
    stackfile.write_text(
        """
version: "1"
guards:
  builtin:
    motion:
      enabled: true
boundaries:
  workspace:
    layer: L2
    type: single
    nodes:
      - node_id: default
        fallback: hold_position
        params:
          upper: [5, 5, 5, 5, 5, 5]
          lower: [-5, -5, -5, -5, -5, -5]
tasks:
  default:
    boundaries: [workspace]
safety:
  control_frequency_hz: 50
  always_active: []
"""
    )
    with pytest.raises(ValueError, match="hardware"):
        LeRobotRunner.from_stackfile_auto(str(stackfile))


# ── from_stackfile with pre-built objects ─────────────────────────────────────


def test_from_stackfile_with_mock_robot(tmp_path):
    """from_stackfile() should wire correctly when given mock robot/policy."""
    stackfile = tmp_path / "test_stack.yaml"
    stackfile.write_text(
        """
version: "1"
guards:
  builtin:
    motion:
      enabled: true
hardware:
  preset: generic_6dof
boundaries:
  workspace:
    layer: L2
    type: single
    nodes:
      - node_id: default
        fallback: hold_position
        params:
          upper: [5, 5, 5, 5, 5, 5]
          lower: [-5, -5, -5, -5, -5, -5]
tasks:
  default:
    boundaries: [workspace]
safety:
  control_frequency_hz: 50
  always_active: []
"""
    )
    robot = MockRobot()
    policy = MockPolicy()
    runner = LeRobotRunner.from_stackfile(str(stackfile), robot, policy)
    assert runner is not None
    results = runner.run("default", n_cycles=3)
    assert len(results) == 3
    assert all(isinstance(r, CycleResult) for r in results)


# ── Hardware preflight check ──────────────────────────────────────────────────


class MockCamera:
    """Configurable mock camera for preflight tests."""

    def __init__(self, *, returns_none=False, raises=None):
        self._returns_none = returns_none
        self._raises = raises

    def read(self):
        if self._raises:
            raise RuntimeError(self._raises)
        return None if self._returns_none else object()


class RobotWithCameras:
    """Robot that exposes a cameras dict and responds to get_observation()."""

    def __init__(self, cameras: dict, *, obs_raises=None, obs_none=False):
        self.cameras = cameras
        self._obs_raises = obs_raises
        self._obs_none = obs_none

    def get_observation(self):
        if self._obs_raises:
            raise RuntimeError(self._obs_raises)
        return None if self._obs_none else {"state": [0.0] * 6}


def test_preflight_passes_when_all_ok():
    robot = RobotWithCameras(
        cameras={"top": MockCamera(), "wrist": MockCamera()},
    )
    # Must not raise
    LeRobotRunner._preflight_check(robot)


def test_preflight_passes_with_no_cameras():
    robot = RobotWithCameras(cameras={})
    LeRobotRunner._preflight_check(robot)


def test_preflight_raises_when_camera_returns_none():
    robot = RobotWithCameras(
        cameras={"top": MockCamera(returns_none=True)},
    )
    with pytest.raises(RuntimeError, match="camera 'top'"):
        LeRobotRunner._preflight_check(robot)


def test_preflight_raises_when_camera_throws():
    robot = RobotWithCameras(
        cameras={"wrist": MockCamera(raises="USB device not found")},
    )
    with pytest.raises(RuntimeError, match="camera 'wrist'"):
        LeRobotRunner._preflight_check(robot)


def test_preflight_raises_when_motor_throws():
    robot = RobotWithCameras(cameras={}, obs_raises="bus error")
    with pytest.raises(RuntimeError, match="motors"):
        LeRobotRunner._preflight_check(robot)


def test_preflight_raises_when_motor_returns_none():
    robot = RobotWithCameras(cameras={}, obs_none=True)
    with pytest.raises(RuntimeError, match="motors"):
        LeRobotRunner._preflight_check(robot)


def test_preflight_collects_all_failures():
    """All failing checks are reported together (not fail-fast)."""
    robot = RobotWithCameras(
        cameras={
            "top": MockCamera(returns_none=True),
            "wrist": MockCamera(raises="timeout"),
        },
        obs_raises="bus error",
    )
    with pytest.raises(RuntimeError) as exc_info:
        LeRobotRunner._preflight_check(robot)
    msg = str(exc_info.value)
    assert "3 issue(s)" in msg
    assert "top" in msg
    assert "wrist" in msg
    assert "motors" in msg


def test_preflight_skipped_when_no_robot_ref():
    """run() skips preflight when robot is None."""
    runtime = make_runtime()

    # Manual setup: register all required adapters
    from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
    from dam.adapter.lerobot.sink import LeRobotSinkAdapter
    from dam.adapter.lerobot.source import LeRobotSourceAdapter

    robot = MockRobot()
    runtime.register_source("arm", LeRobotSourceAdapter(robot))
    runtime.register_sink(LeRobotSinkAdapter(robot))
    runtime.register_policy(LeRobotPolicyAdapter(MockPolicy()))

    runner = LeRobotRunner(runtime=runtime, robot=None)
    assert runner._robot is None
    # Should complete without calling _preflight_check
    results = runner.run("pick_and_place", n_cycles=1)
    assert len(results) == 1
