"""
Adversarial safety regression — see docs/adversarial-testing.md.

Each test encodes a concrete attack and asserts the SAFE outcome. A guard
that can be bypassed by these inputs fails the build.
"""

from __future__ import annotations

import numpy as np
import pytest

from dam.boundary.callbacks import (
    cartesian_velocity_limit,
    joint_position_limits,
    joint_velocity_limit,
    keep_out_zone,
    orientation_limit,
)
from dam.guard.aggregator import aggregate_decisions
from dam.guard.layer import GuardLayer
from dam.types.observation import Observation
from dam.types.result import GuardDecision, GuardResult

NON_FINITE = [np.nan, np.inf, -np.inf]


def _obs(*, jp=None, jv=None, ee=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6) if jp is None else np.asarray(jp, dtype=float),
        joint_velocities=None if jv is None else np.asarray(jv, dtype=float),
        end_effector_pose=None if ee is None else np.asarray(ee, dtype=float),
    )


def _is_pass(result) -> bool:
    """A callback 'passes' when it returns True (or (True, …))."""
    if isinstance(result, tuple):
        return bool(result[0])
    return result is True


class _FakeDynamics:
    """Minimal pinocchio-like context (identity Jacobian)."""

    available = True
    default_frame_id = 0

    def update(self, q):  # noqa: D401 - stub
        pass

    def frame_jacobian(self, _fid):
        return np.eye(6)


# ── T1: non-finite injection must never PASS ──────────────────────────────────


class TestNonFiniteInjection:
    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_joint_position_limits(self, bad):
        obs = _obs(jp=[bad, 0, 0, 0, 0, 0])
        assert not _is_pass(joint_position_limits(obs=obs, upper=np.ones(6), lower=-np.ones(6)))

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_joint_velocity_limit(self, bad):
        obs = _obs(jv=[bad, 0, 0, 0, 0, 0])
        assert not _is_pass(joint_velocity_limit(obs=obs, max_velocities=[1.0] * 6))

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_keep_out_zone(self, bad):
        obs = _obs(ee=[bad, 0.0, 0.0, 0, 0, 0, 1])
        assert not _is_pass(keep_out_zone(obs=obs, boxes=[[[-1, 1], [-1, 1], [-1, 1]]]))

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_orientation_limit(self, bad):
        obs = _obs(ee=[0, 0, 0.3, bad, 0, 0, 1])
        assert not _is_pass(orientation_limit(obs=obs, max_tilt_deg=30.0))

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_cartesian_velocity_limit(self, bad):
        obs = _obs(jp=[0.0] * 6, jv=[bad, 0, 0, 0, 0, 0])
        assert not _is_pass(
            cartesian_velocity_limit(obs=obs, dynamics=_FakeDynamics(), max_linear_speed=0.25)
        )


# ── T2: boundary skimming (float-epsilon edges) ───────────────────────────────


class TestBoundarySkimming:
    EPS = 1e-9

    def test_just_outside_upper_blocked(self):
        obs = _obs(jp=[1.0 + self.EPS, 0, 0, 0, 0, 0])
        assert not _is_pass(joint_position_limits(obs=obs, upper=np.ones(6), lower=-np.ones(6)))

    def test_just_inside_upper_passes(self):
        obs = _obs(jp=[1.0 - self.EPS, 0, 0, 0, 0, 0])
        assert _is_pass(joint_position_limits(obs=obs, upper=np.ones(6), lower=-np.ones(6)))

    def test_velocity_just_over_blocked(self):
        obs = _obs(jv=[1.0 + 1e-6, 0, 0, 0, 0, 0])
        assert not _is_pass(joint_velocity_limit(obs=obs, max_velocities=[1.0] * 6))


# ── T3 / T4: aggregator dominance & fail-to-reject ────────────────────────────


def _r(decision: GuardDecision) -> GuardResult:
    if decision == GuardDecision.PASS:
        return GuardResult.success("g", GuardLayer.L1)
    if decision == GuardDecision.REJECT:
        return GuardResult.reject("nope", "g", GuardLayer.L1)
    if decision == GuardDecision.FAULT:
        return GuardResult.fault(RuntimeError("boom"), "g", "g", GuardLayer.L1)
    raise AssertionError(decision)


class TestAggregatorDominance:
    def test_single_reject_dominates_many_pass(self):
        results = [_r(GuardDecision.PASS)] * 5 + [_r(GuardDecision.REJECT)]
        assert aggregate_decisions(results).decision == GuardDecision.REJECT

    def test_fault_is_at_least_reject(self):
        agg = aggregate_decisions([_r(GuardDecision.PASS), _r(GuardDecision.FAULT)])
        assert agg.decision >= GuardDecision.REJECT

    def test_all_pass_is_pass(self):
        assert aggregate_decisions([_r(GuardDecision.PASS)] * 3).decision == GuardDecision.PASS
