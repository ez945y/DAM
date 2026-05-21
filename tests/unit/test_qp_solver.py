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


def _motion_container(callback, params):
    """Minimal active-container stub exposing get_active_node().constraint."""
    from dataclasses import dataclass
    from typing import Any

    @dataclass
    class _Constraint:
        callback: str | None
        params: dict[str, Any]

    @dataclass
    class _Node:
        node_id: str
        constraint: _Constraint | None

    class _Container:
        def __init__(self, name, node):
            self.name = name
            self._node = node

        def get_active_node(self):
            return self._node

    return _Container(callback, _Node(f"{callback}/0", _Constraint(callback, params)))


def test_motion_guard_with_qp_aggregator_clamps_into_box(proxsuite):
    """Phase 3: QP is re-introduced as an injectable clamp_aggregator. The L1
    callbacks attach QP metadata; MotionGuard(clamp_aggregator=motion_qp_aggregator)
    drives the joint QP and the final GuardResult is a CLAMP."""
    from dam.boundary.callbacks._registry import register_all
    from dam.guard.aggregators.motion_qp import METADATA_KEY, motion_qp_aggregator
    from dam.guard.builtin.motion import MotionGuard
    from dam.guard.layer import GuardLayer
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    register_all()
    MotionGuard._guard_layer = GuardLayer.L1

    container = _motion_container(
        "joint_position_limits",
        {"upper": [1.0] * 6, "lower": [-1.0] * 6},
    )
    obs = Observation(timestamp=0.0, joint_positions=np.zeros(6))
    # Proposal blows past the +1.0 / -1.0 box on two joints.
    action = ActionProposal(target_joint_positions=np.array([2.0, -2.0, 0.0, 0.0, 0.0, 0.0]))

    guard = MotionGuard(clamp_aggregator=motion_qp_aggregator)
    result = guard.check(obs, action, active_containers=[container], dt=0.02)

    assert result.decision == GuardDecision.CLAMP
    assert result.clamped_action is not None
    u = result.clamped_action.target_joint_positions
    assert u[0] <= 1.01 and u[1] >= -1.01
    # The QP path is observable: the callback's QP metadata surfaces on the result.
    assert METADATA_KEY in result.metadata["joint_position_limits"]


def test_motion_guard_default_aggregator_does_not_need_qp(proxsuite):
    """Without the QP aggregator, MotionGuard still box-clamps via the default
    sequential strategy — QP metadata is simply ignored."""
    from dam.boundary.callbacks._registry import register_all
    from dam.guard.builtin.motion import MotionGuard
    from dam.guard.layer import GuardLayer
    from dam.types.action import ActionProposal
    from dam.types.observation import Observation
    from dam.types.result import GuardDecision

    register_all()
    MotionGuard._guard_layer = GuardLayer.L1

    container = _motion_container(
        "joint_position_limits",
        {"upper": [1.0] * 6, "lower": [-1.0] * 6},
    )
    obs = Observation(timestamp=0.0, joint_positions=np.zeros(6))
    action = ActionProposal(target_joint_positions=np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0]))

    result = MotionGuard().check(obs, action, active_containers=[container], dt=0.02)
    assert result.decision == GuardDecision.CLAMP
    assert result.clamped_action.target_joint_positions[0] <= 1.01


def test_qp_solver_param_inlined_in_any_l1_boundary(proxsuite):
    """``qp_solver`` is just another pool param.  Inlining it alongside
    ``upper`` / ``lower`` on an existing L1 boundary is enough to flip
    MotionGuard onto the QP path — no marker callback, no dedicated
    boundary, uniform with every other param."""
    import tempfile

    import yaml

    from dam.config.loader import StackfileLoader
    from dam.runtime.guard_runtime import GuardRuntime

    stack = {
        "version": "1",
        "hardware": {
            "preset": "generic_6dof",
            "joints": {f"j{i}": {} for i in range(6)},
            "sources": {"arm": {"type": "lerobot", "port": "/dev/null"}},
            "sinks": {"cmd": {"ref": "sources.arm"}},
        },
        "safety": {"control_frequency_hz": 30, "no_task_behavior": "emergency_stop"},
        "guards": [{"L1": "motion"}],
        "boundaries": {
            "joint_position_limits": {
                "layer": "L1",
                "type": "single",
                "nodes": [
                    {
                        "callback": "joint_position_limits",
                        "fallback": "emergency_stop",
                        "params": {
                            "upper": [1.0] * 6,
                            "lower": [-1.0] * 6,
                            "qp_solver": "proxsuite",
                            "slack_weight": 1.0e8,
                        },
                    }
                ],
            },
        },
        "tasks": {"default": {"description": "", "boundaries": ["joint_position_limits"]}},
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.safe_dump(stack, f)
        path = f.name

    config = StackfileLoader.load(path)
    pool = GuardRuntime._build_config_pool(config)
    assert pool["qp_solver"] == "proxsuite"
    assert pool["slack_weight"] == 1.0e8
    assert pool["upper"] == [1.0] * 6
    # dt auto-injected from safety.control_frequency_hz
    assert pool["dt"] == pytest.approx(1.0 / 30.0)


def test_workspace_cbf_keeps_action_inside_box(proxsuite):
    """CBF constraints from a Jacobian + bounds prevent the action from
    pushing the EE out of the workspace box."""
    from dam.runtime.qp_solver import solve_box_with_slack, workspace_cbf_constraints

    # Simple 2-DoF planar arm: J_linear maps q̇ → ee velocity.  Take
    # J = I so u directly perturbs ee_pos.
    n = 2
    q = np.zeros(n)
    ee = np.array([0.0, 0.0, 0.5])  # already at z=0.5
    J_lin = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])  # u affects x, y only
    bounds = np.array([[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]])  # ee_z in [0.02, 0.6]

    A_extra, b_extra = workspace_cbf_constraints(
        q=q, ee_pos=ee, J_linear=J_lin, bounds=bounds, cbf_alpha=1.0, dt=0.02
    )
    # 6 rows = 3 upper + 3 lower bounds
    assert A_extra.shape == (6, n)
    assert b_extra.shape == (6,)

    # Nominal command pushes ee_x to 1.0 (way outside [-0.4, 0.4])
    u_nom = np.array([1.0, 0.0])
    u_qp = solve_box_with_slack(
        u_nom,
        slack_weight=1e8,
        extra_A=A_extra,
        extra_ub=b_extra,
    )
    assert u_qp is not None
    # CBF pulled u back so projected ee_x stays at-or-below 0.4
    projected_ee_x = ee[0] + (J_lin @ (u_qp - q))[0]
    assert projected_ee_x <= 0.41
