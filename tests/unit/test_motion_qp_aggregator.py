"""Tests for the opt-in QP clamp aggregator (dam/guard/aggregators/motion_qp.py).

The QP solve itself is gated on ``proxsuite``; tests that need a real solve use
the ``proxsuite`` fixture (skips when absent).  Fallback / alias / validation
behaviour is tested without the solver.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from dam.guard.aggregators.motion_qp import (
    METADATA_KEY,
    MotionQPConstraint,
    motion_qp_aggregator,
)
from dam.guard.pipeline import CallbackResult
from dam.types.action import ActionProposal, ValidatedAction


@pytest.fixture
def proxsuite():
    return pytest.importorskip("proxsuite")


def _clamp(u_nom, *, meta=None, clamped=None, name="b") -> CallbackResult:
    proposal = ActionProposal(target_joint_positions=np.asarray(u_nom, dtype=np.float64))
    action = ValidatedAction(
        target_joint_positions=np.asarray(clamped if clamped is not None else u_nom, dtype=float),
        was_clamped=True,
        original_proposal=proposal,
    )
    metadata = {METADATA_KEY: meta} if meta is not None else None
    return CallbackResult.clamp(name, action, metadata=metadata)


# ── 1. no QP metadata → sequential fallback ───────────────────────────────────


def test_no_qp_metadata_falls_back_to_sequential() -> None:
    # Two plain clamps, no metadata. Sequential takes the most-restrictive per joint.
    c1 = _clamp([2.0, 2.0], clamped=[1.0, 2.0], name="a")
    c2 = _clamp([2.0, 2.0], clamped=[2.0, 1.0], name="b")
    out = motion_qp_aggregator([c1, c2], None)
    assert out is not None
    # merge_restrictive: joint0 from a (1.0), joint1 from b (1.0)
    assert np.allclose(out.target_joint_positions, [1.0, 1.0])


# ── 2. position box via QP ────────────────────────────────────────────────────


def test_position_box_clamped_via_qp(proxsuite) -> None:
    meta = MotionQPConstraint(
        upper=np.array([1.0, 1.0, 1.0]),
        lower=np.array([-1.0, -1.0, -1.0]),
        slack_weight=1e8,
    )
    c = _clamp([2.0, -2.0, 0.0], meta=meta)
    out = motion_qp_aggregator([c], None)
    assert out is not None
    u = out.target_joint_positions
    assert 0.9 < u[0] < 1.01
    assert -1.01 < u[1] < -0.9
    assert abs(u[2]) < 1e-3
    assert out.was_clamped


# ── 3. velocity limit via QP ──────────────────────────────────────────────────


def test_velocity_limit_clamped_via_qp(proxsuite) -> None:
    meta = MotionQPConstraint(
        max_velocity=np.array([0.5, 0.5, 0.5]),
        q=np.array([0.0, 0.0, 0.0]),
        dt=1.0,
        slack_weight=1e8,
    )
    c = _clamp([2.0, 2.0, 2.0], meta=meta)
    out = motion_qp_aggregator([c], None)
    assert out is not None
    # q ± v_max·dt = ±0.5
    assert np.all(out.target_joint_positions <= 0.51)
    assert np.all(out.target_joint_positions >= -0.51)


# ── 4. workspace CBF consumed by aggregator ───────────────────────────────────


def test_workspace_cbf_consumed(proxsuite) -> None:
    J_lin = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])  # u perturbs x, y
    meta = MotionQPConstraint(
        workspace_bounds=np.array([[-0.4, 0.4], [-0.4, 0.4], [0.02, 0.6]]),
        cbf_gamma=1.0,
        ee_pos=np.array([0.0, 0.0, 0.5]),
        q=np.array([0.0, 0.0]),
        J_linear=J_lin,
        slack_weight=1e8,
    )
    c = _clamp([1.0, 0.0], meta=meta)  # nominal pushes ee_x way past 0.4
    out = motion_qp_aggregator([c], None)
    assert out is not None
    projected_ee_x = 0.0 + (J_lin @ (out.target_joint_positions - np.zeros(2)))[0]
    assert projected_ee_x <= 0.41


# ── 5. QP metadata present but proxsuite unavailable → raise ──────────────────


def test_raises_when_proxsuite_unavailable(monkeypatch) -> None:
    from dam.runtime import qp_solver

    monkeypatch.setattr(qp_solver, "available", lambda: False)
    meta = MotionQPConstraint(upper=np.array([1.0]), lower=np.array([-1.0]))
    c = _clamp([2.0], meta=meta)
    with pytest.raises(RuntimeError, match="proxsuite"):
        motion_qp_aggregator([c], None)


def test_no_metadata_does_not_raise_without_proxsuite(monkeypatch) -> None:
    """No QP metadata → pure sequential, regardless of solver availability."""
    from dam.runtime import qp_solver

    monkeypatch.setattr(qp_solver, "available", lambda: False)
    c = _clamp([2.0, 2.0], clamped=[1.0, 1.0])
    out = motion_qp_aggregator([c], None)
    assert out is not None
    assert np.allclose(out.target_joint_positions, [1.0, 1.0])


# ── 6 & 7. cbf_gamma validation / cbf_alpha alias (workspace callback) ─────────


def _obs_outside_box():
    from dam.types.observation import Observation

    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6),
        end_effector_pose=np.array([1.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]),  # x=1.0 outside box
    )


def test_cbf_gamma_out_of_range_raises() -> None:
    from dam.boundary.callbacks.kinematics import workspace

    action = ActionProposal(target_joint_positions=np.zeros(6))
    with pytest.raises(ValueError, match="cbf_gamma"):
        workspace(obs=_obs_outside_box(), action=action, cbf_gamma=2.0)


def test_cbf_alpha_alias_warns_and_maps_to_gamma(caplog) -> None:
    from dam.boundary.callbacks.kinematics import workspace

    action = ActionProposal(target_joint_positions=np.zeros(6))
    with caplog.at_level(logging.WARNING):
        result = workspace(obs=_obs_outside_box(), action=action, cbf_alpha=0.5)
    assert any("cbf_alpha" in r.message for r in caplog.records)
    assert result.metadata[METADATA_KEY].cbf_gamma == 0.5
