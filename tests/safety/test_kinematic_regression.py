"""
Safety regression: all known dangerous motion scenarios MUST be REJECTED or CLAMPED.
These tests NEVER pass through dangerous actions.
"""

import numpy as np
import pytest

from dam.boundary.callbacks._registry import register_all
from dam.boundary.constraint import BoundaryConstraint
from dam.boundary.node import BoundaryNode
from dam.boundary.single import SingleNodeContainer
from dam.decorators import guard as guard_decorator
from dam.guard.builtin.motion import MotionGuard
from dam.testing.helpers import assert_clamps, inject_and_call
from dam.testing.safety import SafetyScenario, safety_regression
from dam.types.action import ActionProposal
from dam.types.observation import Observation
from dam.types.result import GuardDecision


@pytest.fixture
def KG():
    register_all()
    return guard_decorator("L1")(MotionGuard)


def make_obs(ee=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        joint_velocities=np.zeros(6),
        end_effector_pose=np.array([0.0, 0.0, 0.3, 0.0, 0.0, 0.0, 1.0])
        if ee is None
        else np.array(ee),
    )


def make_action(pos=None, vel=None) -> ActionProposal:
    return ActionProposal(
        target_joint_positions=np.zeros(6) if pos is None else np.array(pos),
        target_joint_velocities=np.array(vel) if vel is not None else None,
    )


def _motion_result(guard, callback: str, params: dict, *, obs: Observation, action: ActionProposal):
    container = SingleNodeContainer(
        BoundaryNode(
            "n0",
            BoundaryConstraint(callback=callback, params=params),
            fallback="hold_position",
        )
    )
    return guard.check(obs=obs, action=action, active_containers=[container], dt=0.02)


def test_joint_positions_within_limits_passes(KG):
    g = KG()
    result = _motion_result(
        g,
        "joint_position_limits",
        {"upper": np.ones(6) * 3.14, "lower": -np.ones(6) * 3.14},
        obs=make_obs(),
        action=make_action([0.1] * 6),
    )
    assert_passes(result)


def test_joint_position_overrun_clamped(KG):
    g = KG()
    result = _motion_result(
        g,
        "joint_position_limits",
        {"upper": np.ones(6), "lower": -np.ones(6)},
        obs=make_obs(),
        action=make_action([5.0] * 6),
    )
    assert_clamps(result)
    assert np.all(result.clamped_action.target_joint_positions <= 1.0)


def test_all_joints_below_lower_limit_clamped(KG):
    g = KG()
    result = _motion_result(
        g,
        "joint_position_limits",
        {"upper": np.ones(6), "lower": -np.ones(6)},
        obs=make_obs(),
        action=make_action([-5.0] * 6),
    )
    assert_clamps(result)
    assert np.all(result.clamped_action.target_joint_positions >= -1.0)


def test_workspace_breach_clamps_to_halt(KG):
    g = KG()
    obs = make_obs(ee=[-1.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0])  # x=-1, out of [0, 0.5]
    result = _motion_result(
        g,
        "workspace",
        {"bounds": np.array([[0.0, 0.5], [0.0, 0.5], [0.0, 1.0]])},
        obs=obs,
        action=make_action([1.0] * 6),
    )
    assert_clamps(result)
    np.testing.assert_allclose(result.clamped_action.target_joint_positions, obs.joint_positions)


def test_velocity_overrun_clamped(KG):
    g = KG()
    result = _motion_result(
        g,
        "joint_velocity_limit",
        {"max_velocities": np.ones(6) * 1.0},
        obs=make_obs(),
        action=make_action(vel=[5.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    )
    assert_clamps(result)
    assert np.all(np.abs(result.clamped_action.target_joint_velocities) <= 1.0 + 1e-9)


def test_clamped_action_always_within_limits(KG):
    """Property: after CLAMP, all joints must be within limits."""
    KG()
    for scale in [2.0, 10.0, 100.0, -2.0, -50.0]:
        g2 = KG()
        result = _motion_result(
            g2,
            "joint_position_limits",
            {"upper": np.ones(6), "lower": -np.ones(6)},
            obs=make_obs(),
            action=make_action([scale] * 6),
        )
        if result.decision == GuardDecision.CLAMP:
            assert np.all(result.clamped_action.target_joint_positions >= -1.0)
            assert np.all(result.clamped_action.target_joint_positions <= 1.0)


def test_safety_regression_batch(KG):
    scenarios = [
        SafetyScenario(
            name="workspace_breach",
            guard_instance=KG(),
            config_pool={},
            runtime_kwargs={
                "active_containers": [
                    SingleNodeContainer(
                        BoundaryNode(
                            "workspace",
                            BoundaryConstraint(
                                callback="workspace",
                                params={"bounds": np.array([[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]])},
                            ),
                            fallback="hold_position",
                        )
                    )
                ],
                "dt": 0.02,
                "obs": make_obs(ee=[-2.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]),
                "action": make_action(),
            },
            expected=GuardDecision.CLAMP,
        ),
        SafetyScenario(
            name="joint_overrun",
            guard_instance=KG(),
            config_pool={},
            runtime_kwargs={
                "active_containers": [
                    SingleNodeContainer(
                        BoundaryNode(
                            "joint_position_limits",
                            BoundaryConstraint(
                                callback="joint_position_limits",
                                params={"upper": np.ones(6), "lower": -np.ones(6)},
                            ),
                            fallback="hold_position",
                        )
                    )
                ],
                "dt": 0.02,
                "obs": make_obs(),
                "action": make_action([10.0] * 6),
            },
            expected=GuardDecision.CLAMP,
        ),
    ]
    safety_regression(scenarios)


# Need to import assert_passes here since it's used in test above
from dam.testing.helpers import assert_passes
