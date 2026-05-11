"""Tests for the optional ProxSuite QP filter + MotionGuard dispatch.

The QP solver is gated by ``pytest.importorskip("proxsuite")`` so this file
turns into skips on machines without the dep — same pattern as the other
optional adapters (pinocchio, rclpy).
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def proxsuite():
    return pytest.importorskip("proxsuite")


def test_solver_available_when_installed(proxsuite):
    from dam.runtime.qp_solver import available

    assert available() is True


def test_no_op_when_already_inside_box(proxsuite):
    from dam.runtime.qp_solver import solve_box_with_slack

    u_nom = np.array([0.1, -0.2, 0.0, 0.3, -0.4, 0.5])
    upper = np.array([1.0] * 6)
    lower = np.array([-1.0] * 6)
    u = solve_box_with_slack(u_nom, upper=upper, lower=lower, slack_weight=1e6)
    assert u is not None
    np.testing.assert_allclose(u, u_nom, atol=1e-5)


def test_clamps_to_boundary_when_outside(proxsuite):
    from dam.runtime.qp_solver import solve_box_with_slack

    # u_nom intentionally violates upper on axis 0, lower on axis 1
    u_nom = np.array([2.0, -2.0, 0.0])
    upper = np.array([1.0, 1.0, 1.0])
    lower = np.array([-1.0, -1.0, -1.0])
    u = solve_box_with_slack(u_nom, upper=upper, lower=lower, slack_weight=1e8)
    assert u is not None
    # axis 0 pulled down to near upper, axis 1 up to near lower
    assert u[0] < 1.01 and u[0] > 0.9
    assert u[1] > -1.01 and u[1] < -0.9
    # untouched axis stays close to nominal
    assert abs(u[2]) < 1e-3


def test_slack_softens_infeasible_overlap(proxsuite):
    """When upper < lower (infeasible without slack), QP still returns a value."""
    from dam.runtime.qp_solver import solve_box_with_slack

    u_nom = np.array([0.0])
    upper = np.array([0.0])
    lower = np.array([0.5])  # impossible: lower > upper
    # Larger slack → must still produce a solution
    u = solve_box_with_slack(u_nom, upper=upper, lower=lower, slack_weight=1e4)
    assert u is not None  # solver didn't crash, slack absorbed the conflict


def test_motion_guard_dispatches_to_qp_when_param_set(proxsuite):
    """`qp_solver="proxsuite"` in kwargs → MotionGuard runs QP."""
    from dam.decorators import guard as guard_decorator
    from dam.guard.builtin.motion import MotionGuard
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    DecoratedMotion = guard_decorator("L1")(MotionGuard)
    guard = DecoratedMotion()

    n = 3
    obs = Observation(
        timestamp=0.0,
        joint_positions=np.zeros(n),
        joint_velocities=np.zeros(n),
    )
    # Proposed action violates upper bound
    action = ActionProposal(target_joint_positions=np.array([2.0, 0.0, 0.0]))

    upper = np.array([1.0] * n)
    lower = np.array([-1.0] * n)

    result = guard.check(
        obs,
        action,
        upper=upper,
        lower=lower,
        qp_solver="proxsuite",
        slack_weight=1e8,
    )
    assert result.decision == GuardDecision.CLAMP
    assert "proxsuite" in (result.reason or "")
    clamped = result.clamped_action.target_joint_positions
    assert clamped[0] <= 1.01  # respects upper
    # The two unconstrained axes shouldn't have moved
    assert abs(clamped[1]) < 1e-3
    assert abs(clamped[2]) < 1e-3


def test_motion_guard_falls_back_to_box_clamp_when_no_qp_param(proxsuite):
    """Without `qp_solver`, MotionGuard uses the original box-clamp path."""
    from dam.decorators import guard as guard_decorator
    from dam.guard.builtin.motion import MotionGuard
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    DecoratedMotion = guard_decorator("L1")(MotionGuard)
    guard = DecoratedMotion()

    n = 3
    obs = Observation(
        timestamp=0.0,
        joint_positions=np.zeros(n),
        joint_velocities=np.zeros(n),
    )
    action = ActionProposal(target_joint_positions=np.array([2.0, 0.0, 0.0]))
    upper = np.array([1.0] * n)
    lower = np.array([-1.0] * n)

    result = guard.check(obs, action, upper=upper, lower=lower)
    assert result.decision == GuardDecision.CLAMP
    # box-clamp path doesn't mention proxsuite
    assert "proxsuite" not in (result.reason or "")
