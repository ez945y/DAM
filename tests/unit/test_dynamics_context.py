"""Tests for DynamicsContext — shared FK/Jacobian state."""

from __future__ import annotations

import numpy as np
import pytest

from dam.types.dynamics import DynamicsContext


def test_unavailable_sentinel():
    ctx = DynamicsContext.unavailable()
    assert ctx.available is False
    assert ctx.nq == 0
    # update is a safe no-op
    ctx.update(np.zeros(6))
    # frame_jacobian raises so guards must check `available` first
    with pytest.raises(RuntimeError):
        ctx.frame_jacobian(0)


def _make_two_joint_model():
    """Build a tiny 2-DoF pinocchio model with a frame at the tip."""
    pin = pytest.importorskip("pinocchio")
    model = pin.Model()
    parent = 0
    for i in range(2):
        parent = model.addJoint(parent, pin.JointModelRZ(), pin.SE3.Identity(), f"j{i}")
        model.appendBodyToJoint(parent, pin.Inertia.Random(), pin.SE3.Identity())
    last_frame = pin.Frame("ee", parent, 0, pin.SE3.Identity(), pin.FrameType.OP_FRAME)
    fid = model.addFrame(last_frame)
    return model, model.createData(), fid


def test_update_caches_for_same_q(monkeypatch):
    """Repeated update(q) with the same array must not call pinocchio twice."""
    pin = pytest.importorskip("pinocchio")
    model, data, _fid = _make_two_joint_model()
    ctx = DynamicsContext(model=model, data=data, joint_names=["j0", "j1"])

    call_count = {"n": 0}
    real_fn = pin.computeJointJacobians

    def counting_compute(m, d, q):
        call_count["n"] += 1
        return real_fn(m, d, q)

    monkeypatch.setattr(pin, "computeJointJacobians", counting_compute)

    q = np.array([0.1, 0.2])
    ctx.update(q)
    ctx.update(q)  # same q — no-op
    ctx.update(q.copy())  # different ndarray, same values — still no-op
    assert call_count["n"] == 1


def test_jacobian_cache_per_frame():
    pytest.importorskip("pinocchio")
    model, data, fid = _make_two_joint_model()
    ctx = DynamicsContext(model=model, data=data, joint_names=["j0", "j1"])
    ctx.update(np.array([0.1, 0.2]))

    J1 = ctx.frame_jacobian(fid)
    J2 = ctx.frame_jacobian(fid)
    # Same object identity → real cache, not just equal values
    assert J1 is J2
    assert J1.shape == (6, 2)


def test_update_cache_invalidates_on_new_q():
    pytest.importorskip("pinocchio")
    model, data, fid = _make_two_joint_model()
    ctx = DynamicsContext(model=model, data=data, joint_names=["j0", "j1"])

    ctx.update(np.array([0.0, 0.0]))
    J0 = ctx.frame_jacobian(fid)
    ctx.update(np.array([1.0, 1.0]))
    J1 = ctx.frame_jacobian(fid)
    assert J0 is not J1  # cache flushed → new array


def test_update_pads_short_q():
    """q shorter than nq should be zero-padded, not crash pinocchio."""
    pytest.importorskip("pinocchio")
    model, data, fid = _make_two_joint_model()
    ctx = DynamicsContext(model=model, data=data, joint_names=["j0", "j1"])
    assert ctx.nq == 2

    ctx.update(np.array([0.5]))
    placement = ctx.frame_placement(fid)
    assert placement.translation.shape == (3,)


def test_update_truncates_long_q():
    """q longer than nq should be truncated, not crash pinocchio."""
    pytest.importorskip("pinocchio")
    model, data, fid = _make_two_joint_model()
    ctx = DynamicsContext(model=model, data=data, joint_names=["j0", "j1"])

    ctx.update(np.array([0.1, 0.2, 0.3, 0.4]))
    placement = ctx.frame_placement(fid)
    assert placement.translation.shape == (3,)
