"""Tests for the AckermannSolver planar [v, omega] rollout."""

from __future__ import annotations

import math

import numpy as np
import pytest

from dam.kinematics.ackermann import AckermannSolver


def test_straight_line_rollout():
    solver = AckermannSolver()
    nxt = solver.rollout([0.0, 0.0, 0.0], [1.0, 0.0], dt=2.0)
    assert nxt == pytest.approx([2.0, 0.0, 0.0])


def test_pure_rotation_keeps_position():
    solver = AckermannSolver()
    nxt = solver.rollout([1.0, 2.0, 0.0], [0.0, 1.0], dt=math.pi / 2)
    assert nxt[:2] == pytest.approx([1.0, 2.0])
    assert nxt[2] == pytest.approx(math.pi / 2)


def test_quarter_circle_arc():
    # v=1, omega=1 for pi/2 s → radius-1 left turn: (0,0,0) → (1,1,pi/2).
    solver = AckermannSolver()
    nxt = solver.rollout([0.0, 0.0, 0.0], [1.0, 1.0], dt=math.pi / 2)
    assert nxt == pytest.approx([1.0, 1.0, math.pi / 2])


def test_batched_rollout():
    solver = AckermannSolver()
    states = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, math.pi / 2]])
    commands = np.array([[1.0, 0.0], [1.0, 0.0]])
    out = solver.rollout(states, commands, dt=1.0)
    assert out.shape == (2, 3)
    assert out[0] == pytest.approx([1.0, 0.0, 0.0])
    assert out[1] == pytest.approx([0.0, 1.0, math.pi / 2])


def test_steering_to_yaw_rate_requires_wheel_base():
    with pytest.raises(ValueError):
        AckermannSolver().steering_to_yaw_rate(1.0, 0.1)
    solver = AckermannSolver(wheel_base=0.5)
    omega = solver.steering_to_yaw_rate(2.0, math.atan(0.5 * 1.0 / 2.0))
    assert float(omega) == pytest.approx(1.0)


def test_registered_as_builtin_solver():
    from dam.solver.builtin import register_all
    from dam.solver.registry import get_global_solver_registry

    register_all()
    solver = get_global_solver_registry().build("base_kinematics", "ackermann", {})
    assert isinstance(solver, AckermannSolver)
    assert "rollout" in solver._dam_solver_capabilities


def test_solver_key_is_type_without_type_field():
    """A solvers block with no `type` resolves the implementation by its key."""
    from dam.config.schema import StackfileConfig
    from dam.runtime._stackfile_builder import _init_solvers

    cfg = StackfileConfig(
        **{
            "version": "1",
            "hardware": {"solvers": {"ackermann": {"capabilities": ["rollout"]}}},
            "tasks": {"default": {"boundaries": []}},
        }
    )
    solvers = _init_solvers(cfg)
    assert isinstance(solvers["ackermann"], AckermannSolver)
