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
from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.risk import CycleResult


def make_runtime(upper=2.0, lower=-2.0) -> GuardRuntime:
    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    build_escalation_chain(reg)
    node = BoundaryNode("n0", BoundaryConstraint(), fallback="hold_position")
    g.set_name("main")
    container = SingleNodeContainer(node)
    runtime = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"task": ["main"]},
        config_pool={
            "upper": np.full(6, upper),
            "lower": np.full(6, lower),
        },
    )
    return runtime


def make_obs(pos=None, ee=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6) if pos is None else np.array(pos),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.array([0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0])
        if ee is None
        else np.array(ee),
    )


def make_action(pos=None) -> ActionProposal:
    return ActionProposal(
        target_joint_positions=np.zeros(6) if pos is None else np.array(pos),
    )


def test_pass_through():
    runtime = make_runtime()
    runtime.start_task("task")
    obs = make_obs()
    action = make_action()
    validated, results, fallback = runtime.validate(obs, action, "test-trace")
    assert validated is not None
    assert not validated.was_clamped
    assert fallback is None
    assert len(results) == 1


def test_clamp_joint_position_limits():
    runtime = make_runtime(upper=1.0, lower=-1.0)
    runtime.start_task("task")
    obs = make_obs()
    action = make_action([3.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # First joint way out
    validated, results, fallback = runtime.validate(obs, action, "test-trace")
    assert validated is not None
    assert validated.was_clamped
    assert validated.target_joint_positions[0] == pytest.approx(1.0)
    assert fallback is None


def test_reject_returns_none():
    make_runtime()
    # Workspace bounds rejection
    node = BoundaryNode(
        "n0",
        BoundaryConstraint(params={"bounds": [[0, 0.5], [0, 0.5], [0, 0.5]]}),
        fallback="emergency_stop",
    )
    container = SingleNodeContainer(node)
    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    build_escalation_chain(reg)
    runtime2 = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"task": ["main"]},
        config_pool={
            "upper": np.full(6, 5.0),
            "lower": np.full(6, -5.0),
            "bounds": np.array([[0, 0.5], [0, 0.5], [0, 0.5]]),
        },
    )
    g.set_name("main")
    obs = make_obs(ee=[-1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])  # Outside workspace
    action = make_action()
    runtime2.start_task("task")
    validated, results, fallback = runtime2.validate(obs, action, "test-trace")
    assert validated is None
    assert fallback is not None


def test_full_step_cycle():
    runtime = make_runtime()
    runtime.start_task("task")
    obs = make_obs()
    action = make_action()
    source = MockSourceAdapter([obs, obs, obs])
    policy = MockPolicyAdapter([action, action, action])
    sink = MockSinkAdapter()
    runtime.register_source("main", source)
    runtime.register_policy(policy)
    runtime.register_sink(sink)

    results = [runtime.step() for _ in range(3)]
    runtime.stop_task()

    assert len(results) == 3
    assert all(isinstance(r, CycleResult) for r in results)
    assert len(sink.received) == 3


def test_guard_exception_causes_reject():
    class BrokenGuard(MotionGuard):
        def check(self, **kwargs):  # type: ignore[override]
            raise RuntimeError("Guard exploded!")

    BG = guard_decorator("L2")(BrokenGuard)
    BG._cached_param_names = ["obs", "action"]
    g = BG()
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    build_escalation_chain(reg)
    node = BoundaryNode("n0", BoundaryConstraint(), fallback="emergency_stop")
    container = SingleNodeContainer(node)
    runtime = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"task": ["main"]},
        config_pool={},
    )
    g.set_name("main")
    obs = make_obs()
    action = make_action()
    runtime.start_task("task")
    validated, results, fallback = runtime.validate(obs, action, "test-trace")
    assert validated is None  # Exception → FAULT → REJECT → None
    assert fallback is not None
