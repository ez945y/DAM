"""Safety regression: execution guard known dangerous scenarios."""

import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.guard.builtin.execution import ExecutionGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture
def EG():
    return guard_decorator("L3")(ExecutionGuard)


def make_obs_inside():
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.array([0.05] * 6),  # slow
        end_effector_pose=np.array([0.1, 0.1, 0.2, 0.0, 0.0, 0.0, 1.0]),
    )


def make_container(max_speed=0.5, bounds=None):
    params = {}
    if max_speed is not None:
        params["max_speed"] = max_speed
    if bounds is not None:
        params["bounds"] = bounds
    c = BoundaryConstraint(params=params)
    node = BoundaryNode("n0", c, fallback="hold_position")
    return SingleNodeContainer(node)


def test_normal_cycle_passes(EG):
    g = EG()
    precompute_injection(g, {})
    container = make_container(max_speed=1.0)
    result = g.check(obs=make_obs_inside(), active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.PASS


def test_overspeed_always_rejected(EG):
    g = EG()
    precompute_injection(g, {})
    for speed in [2.0, 5.0, 10.0]:
        g2 = EG()
        precompute_injection(g2, {})
        container = make_container(max_speed=0.1)
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.zeros(6),
            joint_velocities=np.full(6, speed),
            end_effector_pose=np.zeros(7),
        )
        result = g2.check(obs=obs, active_containers=[container], node_start_times={})
        assert result.decision == GuardDecision.REJECT, f"speed={speed} should be REJECT"


def test_workspace_violation_always_rejected(EG):
    g = EG()
    precompute_injection(g, {})
    container = make_container(bounds=[[0, 0.5], [0, 0.5], [0, 0.5]])
    for pos in [[-1, 0, 0], [0, -1, 0], [0, 0, 2.0]]:
        g2 = EG()
        precompute_injection(g2, {})
        obs = Observation(
            timestamp=0.0,
            joint_positions=np.zeros(6),
            joint_velocities=np.zeros(6),
            end_effector_pose=np.array(pos + [0.0, 0.0, 0.0, 1.0]),
        )
        result = g2.check(obs=obs, active_containers=[container], node_start_times={})
        assert result.decision == GuardDecision.REJECT, f"pos={pos} should be REJECT"
