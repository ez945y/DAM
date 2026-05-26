import numpy as np
import pytest

from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.list_container import ListContainer
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.guard.builtin.execution import ExecutionGuard
from dam.guard.builtin.motion import MotionGuard
from dam.runtime.guard_runtime import GuardRuntime
from dam.testing.mocks import MockPolicyAdapter, MockSinkAdapter, MockSourceAdapter
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision
from dam.types.risk import CycleResult

LEFT_PICK_ZONE = [[-0.175, -0.025], [-0.075, 0.075], [0.075, 0.225]]
RIGHT_PLACE_ZONE = [[0.025, 0.175], [-0.075, 0.075], [0.075, 0.225]]


def make_runtime(upper=2.0, lower=-2.0) -> GuardRuntime:
    from dam.boundary.callbacks._registry import register_all

    register_all()
    KG = guard_decorator("L1")(MotionGuard)
    g = KG()
    node = BoundaryNode(
        "n0",
        BoundaryConstraint(
            callback="joint_position_limits",
            params={"upper": np.full(6, upper), "lower": np.full(6, lower)},
        ),
        fallback="hold_position",
    )
    g.set_name("main")
    container = SingleNodeContainer(node)
    runtime = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        task_config={"task": ["main"]},
        config_pool={},
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


def make_gripper_action(value: float) -> ActionProposal:
    return ActionProposal(target_joint_positions=np.zeros(6), gripper_action=value)


def make_l2_gripper_sequence_runtime() -> GuardRuntime:
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    guard_cls = guard_decorator("L2")(ExecutionGuard)
    guard = guard_cls()
    guard.set_name("task_gripper_sequence")

    container = ListContainer(
        [
            BoundaryNode(
                "pick_left",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "close", "zone": LEFT_PICK_ZONE},
                ),
                fallback="hold_position",
            ),
            BoundaryNode(
                "transfer_left_to_right",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "none"},
                ),
                fallback="hold_position",
            ),
            BoundaryNode(
                "place_right",
                BoundaryConstraint(
                    callback="task_gripper_command_guard",
                    params={"allowed_command": "open", "zone": RIGHT_PLACE_ZONE},
                ),
                fallback="hold_position",
            ),
        ]
    )

    return GuardRuntime(
        guards=[guard],
        boundary_containers={"task_gripper_sequence": container},
        task_config={"task": ["task_gripper_sequence"]},
        config_pool={},
    )


def assert_l2_decision(
    runtime: GuardRuntime,
    *,
    ee: list[float],
    gripper: float,
    expected: GuardDecision,
    trace_id: str,
) -> None:
    validated, results = runtime.validate(
        make_obs(ee=ee),
        make_gripper_action(gripper),
        trace_id,
    )
    assert results
    assert results[0].decision == expected
    if expected == GuardDecision.CLAMP:
        assert validated is not None
        assert validated.was_clamped
        assert validated.gripper_action is None
    else:
        assert validated is not None


def test_pass_through():
    runtime = make_runtime()
    runtime.start_task("task")
    obs = make_obs()
    action = make_action()
    validated, results = runtime.validate(obs, action, "test-trace")
    assert validated is not None
    assert not validated.was_clamped
    assert len(results) == 1


def test_clamp_joint_position_limits():
    runtime = make_runtime(upper=1.0, lower=-1.0)
    runtime.start_task("task")
    obs = make_obs()
    action = make_action([3.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # First joint way out
    validated, results = runtime.validate(obs, action, "test-trace")
    assert validated is not None
    assert validated.was_clamped
    assert validated.target_joint_positions[0] == pytest.approx(1.0)


def test_reject_returns_none():
    """A non-finite action triggers VIOLATE (→ REJECT) and validate returns None."""
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    runtime = make_runtime()
    runtime.start_task("task")
    obs = make_obs()
    action = ActionProposal(target_joint_positions=np.full(6, float("nan")))
    validated, results = runtime.validate(obs, action, "test-trace")
    assert validated is None


def test_task_gripper_sequence_phase_advance_contract():
    runtime = make_l2_gripper_sequence_runtime()
    runtime.start_task("task")

    assert (
        runtime._boundary_containers["task_gripper_sequence"].get_active_node().node_id
        == "pick_left"
    )
    assert_l2_decision(
        runtime,
        ee=[-0.10, 0.0, 0.15, 0.0, 0.0, 0.0, 1.0],
        gripper=0.0,
        expected=GuardDecision.PASS,
        trace_id="pick-close",
    )
    assert_l2_decision(
        runtime,
        ee=[-0.10, 0.0, 0.15, 0.0, 0.0, 0.0, 1.0],
        gripper=1.0,
        expected=GuardDecision.CLAMP,
        trace_id="pick-open",
    )

    runtime.advance_container("task_gripper_sequence")
    assert (
        runtime._boundary_containers["task_gripper_sequence"].get_active_node().node_id
        == "transfer_left_to_right"
    )
    for gripper, trace_id in [(0.0, "transfer-close"), (1.0, "transfer-open")]:
        assert_l2_decision(
            runtime,
            ee=[0.0, 0.0, 0.15, 0.0, 0.0, 0.0, 1.0],
            gripper=gripper,
            expected=GuardDecision.CLAMP,
            trace_id=trace_id,
        )

    runtime.advance_container("task_gripper_sequence")
    assert (
        runtime._boundary_containers["task_gripper_sequence"].get_active_node().node_id
        == "place_right"
    )
    assert_l2_decision(
        runtime,
        ee=[0.10, 0.0, 0.15, 0.0, 0.0, 0.0, 1.0],
        gripper=1.0,
        expected=GuardDecision.PASS,
        trace_id="place-open",
    )
    assert_l2_decision(
        runtime,
        ee=[0.10, 0.0, 0.15, 0.0, 0.0, 0.0, 1.0],
        gripper=0.0,
        expected=GuardDecision.CLAMP,
        trace_id="place-close",
    )


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
    node = BoundaryNode("n0", BoundaryConstraint(), fallback="emergency_stop")
    container = SingleNodeContainer(node)
    runtime = GuardRuntime(
        guards=[g],
        boundary_containers={"main": container},
        task_config={"task": ["main"]},
        config_pool={},
    )
    g.set_name("main")
    obs = make_obs()
    action = make_action()
    runtime.start_task("task")
    validated, results = runtime.validate(obs, action, "test-trace")
    assert validated is None  # Exception → FAULT → REJECT → None
