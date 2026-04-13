"""Tests for OODGuard — updated for Memory Bank / nn_threshold API."""

import numpy as np
import pytest

from dam.decorators import guard as guard_decorator
from dam.guard.builtin.ood import OODGuard
from dam.injection.static import precompute_injection
from dam.types.observation import Observation
from dam.types.result import GuardDecision


def make_obs(positions=None, velocities=None):
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6) if positions is None else np.array(positions),
        joint_velocities=np.zeros(6) if velocities is None else np.array(velocities),
        end_effector_pose=np.zeros(7),
    )


@pytest.fixture
def OG():
    return guard_decorator("L0")(OODGuard)


def test_ood_passes_during_warmup(OG):
    g = OG()
    precompute_injection(g, {})
    for _ in range(30):
        result = g.check(obs=make_obs(), nn_threshold=0.5)
        assert result.decision == GuardDecision.PASS


def test_ood_passes_normal_obs_after_warmup(OG):
    g = OG()
    precompute_injection(g, {})
    # Warm up with consistent data
    for _ in range(35):
        g.check(obs=make_obs([0.1] * 6), nn_threshold=0.5)
    # Normal observation — should pass (Welford fallback, same distribution)
    result = g.check(obs=make_obs([0.1] * 6), nn_threshold=0.5)
    assert result.decision == GuardDecision.PASS


def test_ood_rejects_extreme_obs_after_warmup(OG):
    g = OG()
    precompute_injection(g, {})
    # Build baseline with small values
    for _ in range(40):
        g.check(obs=make_obs([0.01] * 6), nn_threshold=0.5)
    # Extreme observation — very different from baseline
    result = g.check(obs=make_obs([100.0] * 6), nn_threshold=0.5)
    assert result.decision == GuardDecision.REJECT
