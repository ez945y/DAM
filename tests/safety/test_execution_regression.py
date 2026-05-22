"""Safety regression: execution guard known dangerous scenarios."""

import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.guard.builtin.execution import ExecutionGuard
from dam.injection.static import precompute_injection
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture
def EG():
    # ExecutionGuard declares its layer via @dam.guard(layer="L2") on the class.
    return ExecutionGuard


def make_obs_inside():
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.array([0.05] * 6),  # slow
        end_effector_pose=np.array([0.1, 0.1, 0.2, 0.0, 0.0, 0.0, 1.0]),
    )


def make_container(max_speed=0.5, bounds=None):
    """Task limits are L2 callbacks now; pick the callback for the param given."""
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    if bounds is not None:
        c = BoundaryConstraint(callback="task_workspace_bounds", params={"bounds": bounds})
    else:
        c = BoundaryConstraint(callback="task_joint_speed_limit", params={"max_speed": max_speed})
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


def test_40_task_section_gripper_injections_clamped(EG):
    """Requirement coverage: task-section anomalous data injection.

    Suppresses close before the planned pick zone, open before the planned place
    zone, and repeated open/close commands during movement segments.
    """
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    constraint = BoundaryConstraint(
        callback="task_gripper_command_guard",
        params={
            "pick_zone": [[0.2, 0.4], [0.2, 0.4], [0.1, 0.3]],
            "place_zone": [[0.6, 0.8], [0.0, 0.2], [0.1, 0.3]],
        },
    )
    container = SingleNodeContainer(BoundaryNode("n0", constraint, fallback="hold_position"))

    events = []
    for _i in range(14):
        events.append(("pick", 0.0, [0.0, 0.0, 0.2]))
    for _i in range(13):
        events.append(("place", 1.0, [0.3, 0.3, 0.2]))
    for i in range(13):
        events.append(("move", 0.0 if i % 2 == 0 else 1.0, [0.3, 0.3, 0.2]))
    assert len(events) == 40

    for idx, (segment, gripper, pos) in enumerate(events):
        g = EG()
        precompute_injection(g, {})
        obs = Observation(
            timestamp=float(idx),
            joint_positions=np.zeros(6),
            joint_velocities=np.zeros(6),
            end_effector_pose=np.array(pos + [0.0, 0.0, 0.0, 1.0]),
        )
        action = ActionProposal(
            target_joint_positions=np.zeros(6),
            gripper_action=gripper,
            metadata={"task_segment": segment},
        )
        result = g.check(
            obs=obs,
            action=action,
            active_containers=[container],
            node_start_times={},
        )
        assert result.decision == GuardDecision.CLAMP, f"event {idx} should be CLAMP"
        assert result.clamped_action is not None
        assert result.clamped_action.gripper_action is None
