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
