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


def make_obs(velocities=None, ee=None, force=None):
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6) if velocities is None else np.array(velocities),
        end_effector_pose=np.array([0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 1.0])
        if ee is None
        else np.array(ee),
        force_torque=np.array(force) if force is not None else None,
    )


def make_action():
    return ActionProposal(target_joint_positions=np.zeros(6))


def make_container(callback=None, **params):
    constraint = BoundaryConstraint(callback=callback, params=params)
    node = BoundaryNode(
        "n0", constraint, fallback="hold_position", timeout_sec=params.get("timeout_sec")
    )
    return SingleNodeContainer(node)


def test_no_containers_passes(EG):
    g = EG()
    precompute_injection(g, {})
    result = g.check(obs=make_obs(), active_containers=[], node_start_times={})
    assert result.decision == GuardDecision.PASS


def test_task_speed_within_limit_passes(EG):
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = EG()
    precompute_injection(g, {})
    container = make_container(callback="task_joint_speed_limit", max_speed=10.0)
    obs = make_obs(velocities=[0.1] * 6)  # norm ≈ 0.245
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.PASS


def test_task_speed_exceeded_rejects(EG):
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = EG()
    precompute_injection(g, {})
    container = make_container(callback="task_joint_speed_limit", max_speed=1.0)
    obs = make_obs(velocities=[5.0, 5.0, 5.0, 5.0, 5.0, 5.0])  # norm ≈ 12.2 > 1.0
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.REJECT
    assert "max_speed" in result.reason


def test_task_workspace_bounds_breach_rejects(EG):
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = EG()
    precompute_injection(g, {})
    container = make_container(
        callback="task_workspace_bounds", bounds=[[0, 0.5], [0, 0.5], [0, 0.5]]
    )
    obs = make_obs(ee=[-1.0, 0.0, 0.2, 0.0, 0.0, 0.0, 1.0])  # x=-1.0 outside bounds
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.REJECT


def test_bare_params_without_callback_are_ignored(EG):
    """ExecutionGuard no longer interprets max_speed / bounds itself — without a
    callback those params are inert (the guard only dispatches callbacks +
    timeout). Proves the L1/L2 split: task limits live in callbacks now."""
    g = EG()
    precompute_injection(g, {})
    container = make_container(max_speed=0.001, bounds=[[0, 0.1], [0, 0.1], [0, 0.1]])
    obs = make_obs(velocities=[5.0] * 6, ee=[-1.0, 0.0, 0.2, 0.0, 0.0, 0.0, 1.0])
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.PASS


def test_l3_force_callback_is_ignored_by_execution_guard(EG):
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = EG()
    precompute_injection(g, {})
    container = make_container(callback="check_force_torque_safe", max_force_n=5.0)
    obs = make_obs(force=[20.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # 20N > 5N
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.PASS


def test_timeout_rejects(EG):
    import time

    g = EG()
    precompute_injection(g, {})
    container = make_container(timeout_sec=0.001)  # 1ms timeout
    node_start_times = {"n0": time.monotonic() - 1.0}  # started 1s ago
    result = g.check(
        obs=make_obs(), active_containers=[container], node_start_times=node_start_times
    )
    assert result.decision == GuardDecision.REJECT


def test_callback_with_params_passes(EG):
    from dam.registry.callback import get_global_registry

    reg = get_global_registry()

    def test_cb(obs, threshold=0.5):
        # Pass if max velocity < threshold
        val = float(np.max(np.abs(obs.joint_velocities)))
        return val < threshold

    test_cb._cb_layer = "L2"
    reg.register("test_cb", test_cb)

    g = EG()
    precompute_injection(g, {})

    # 1. Pass case
    constraint = BoundaryConstraint(callback="test_cb", params={"threshold": 1.0})
    node = BoundaryNode("n0", constraint)
    container = SingleNodeContainer(node)

    obs = make_obs(velocities=[0.5] * 6)
    result = g.check(obs=obs, active_containers=[container], node_start_times={})
    assert result.decision == GuardDecision.PASS

    # 2. Reject case
    obs_fail = make_obs(velocities=[1.5] * 6)
    result_fail = g.check(obs=obs_fail, active_containers=[container], node_start_times={})
    assert result_fail.decision == GuardDecision.REJECT
    assert "test_cb" in result_fail.reason
