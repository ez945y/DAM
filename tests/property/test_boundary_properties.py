"""Property-based tests using Hypothesis."""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.list_container import ListContainer
from dam.boundary.node import BoundaryNode


def make_node(node_id: str = "n") -> BoundaryNode:
    return BoundaryNode(node_id=node_id, constraint=BoundaryConstraint())


@given(st.integers(min_value=0, max_value=20))
@settings(max_examples=50)
def test_list_container_snapshot_restore_consistency(n_advances: int):
    """After any sequence of advances, snapshot -> restore -> same state."""
    nodes = [make_node(f"n{i}") for i in range(5)]
    container = ListContainer(nodes)
    for _ in range(n_advances):
        container.advance()
    state = container.snapshot()
    node_before = container.get_active_node().node_id
    container.advance()
    container.restore(state)
    assert container.get_active_node().node_id == node_before


@given(st.floats(min_value=-1000, max_value=1000, allow_nan=False))
@settings(max_examples=100)
def test_motion_clamp_always_within_limits(joint_value: float):
    """Any joint value, after CLAMP, must be within [-1, 1]."""
    from dam.decorators import guard as guard_decorator
    from dam.guard.builtin.motion import MotionGuard
    from dam.injection.static import precompute_injection
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    KG = guard_decorator("L2")(MotionGuard)
    g = KG()
    config_pool = {"upper": np.ones(6), "lower": -np.ones(6)}
    precompute_injection(g, config_pool)
    obs = Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.zeros(7),
    )
    action = ActionProposal(target_joint_positions=np.full(6, joint_value))
    result = g.check(obs=obs, action=action, upper=np.ones(6), lower=-np.ones(6))
    if result.decision == GuardDecision.CLAMP:
        assert result.clamped_action is not None
        assert np.all(result.clamped_action.target_joint_positions >= -1.0 - 1e-9)
        assert np.all(result.clamped_action.target_joint_positions <= 1.0 + 1e-9)
