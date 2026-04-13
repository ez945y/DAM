"""Integration: Phase 2 full pipeline with mock lerobot adapters."""

import numpy as np

from dam.adapter.lerobot.policy import LeRobotPolicyAdapter
from dam.adapter.lerobot.sink import LeRobotSinkAdapter
from dam.adapter.lerobot.source import LeRobotSourceAdapter
from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.fallback.builtin import EmergencyStop, HoldPosition
from dam.fallback.chain import build_escalation_chain
from dam.fallback.registry import FallbackRegistry
from dam.guard.builtin.execution import ExecutionGuard
from dam.guard.builtin.motion import MotionGuard
from dam.runner.lerobot import LeRobotRunner
from dam.runtime.guard_runtime import GuardRuntime
from dam.types.risk import CycleResult, RiskLevel


class MockRobot:
    def __init__(self, positions=None):
        self._pos = positions or [0.1] * 6
        self.received = []

    def capture_observation(self):
        return {"observation.state": self._pos}

    def send_action(self, d):
        self.received.append(d)


class MockPolicy:
    def select_action(self, obs_dict):
        return np.array([0.1] * 6)


def make_runtime_with_guards():
    KG = guard_decorator("L2")(MotionGuard)
    EG = guard_decorator("L3")(ExecutionGuard)
    kg = KG()
    eg = EG()
    reg = FallbackRegistry()
    reg.register(EmergencyStop())
    reg.register(HoldPosition())
    build_escalation_chain(reg)
    node = BoundaryNode(
        "n0",
        BoundaryConstraint(
            params={
                "bounds": [[-0.5, 0.5], [-0.5, 0.5], [0.0, 0.8]],
            }
        ),
        fallback="hold_position",
    )
    kg.set_name("main")
    eg.set_name("main")
    container = SingleNodeContainer(node)
    return GuardRuntime(
        guards=[kg, eg],
        boundary_containers={"main": container},
        fallback_registry=reg,
        task_config={"pick_and_place": ["main"]},
        config_pool={
            "upper": np.full(6, 3.14),
            "lower": np.full(6, -3.14),
        },
    )


def test_phase2_pipeline_10_cycles():
    runtime = make_runtime_with_guards()
    robot = MockRobot()
    policy = MockPolicy()
    runner = LeRobotRunner(
        runtime=runtime,
        source=LeRobotSourceAdapter(robot),
        sink=LeRobotSinkAdapter(robot),
        policy=LeRobotPolicyAdapter(policy),
    )
    results = runner.run("pick_and_place", n_cycles=10)
    assert len(results) == 10
    assert all(isinstance(r, CycleResult) for r in results)
    assert all(r.risk_level in (RiskLevel.NORMAL, RiskLevel.ELEVATED) for r in results)
    # Some actions should reach the sink
    assert len(robot.received) > 0


def test_phase2_pipeline_guard_results_populated():
    """CycleResult.guard_results should contain actual guard outputs."""
    runtime = make_runtime_with_guards()
    robot = MockRobot()
    policy = MockPolicy()
    runner = LeRobotRunner(
        runtime=runtime,
        source=LeRobotSourceAdapter(robot),
        sink=LeRobotSinkAdapter(robot),
        policy=LeRobotPolicyAdapter(policy),
    )
    runner.start_task("pick_and_place")
    result = runner.step()
    assert len(result.guard_results) > 0  # should have MotionGuard + ExecutionGuard results
    runner.stop()
