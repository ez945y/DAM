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

LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]


@pytest.fixture
def EG():
    return ExecutionGuard


@pytest.fixture(autouse=True)
def _register():
    from dam.boundary.builtin_callbacks import register_all

    register_all()


def test_40_task_section_gripper_injections_clamped(EG):
    """Requirement coverage: task-section anomalous data injection.

    Suppresses close outside the planned left pick zone, open outside the
    planned right place zone, and repeated open/close commands during transfer.
    """
    containers = {
        "pick": SingleNodeContainer(
            BoundaryNode(
                "pick",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "close", "zone": LEFT_PICK_ZONE},
                ),
                fallback="hold_position",
            )
        ),
        "move": SingleNodeContainer(
            BoundaryNode(
                "move",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "none"},
                ),
                fallback="hold_position",
            )
        ),
        "place": SingleNodeContainer(
            BoundaryNode(
                "place",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "open", "zone": RIGHT_PLACE_ZONE},
                ),
                fallback="hold_position",
            )
        ),
    }

    events = []
    for _i in range(14):
        events.append(("pick", 0.0, [0.10, 0.0, 0.15]))
    for _i in range(13):
        events.append(("place", 1.0, [-0.10, 0.0, 0.15]))
    for i in range(13):
        events.append(("move", 0.0 if i % 2 == 0 else 1.0, [0.0, 0.0, 0.15]))
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
        )
        result = g.check(
            obs=obs,
            action=action,
            active_containers=[containers[segment]],
            node_start_times={},
        )
        assert result.decision == GuardDecision.CLAMP, f"event {idx} should be CLAMP"
        assert result.clamped_action is not None
        assert result.clamped_action.gripper_action is None
