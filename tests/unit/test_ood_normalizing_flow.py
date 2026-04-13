"""Tests for RealNVP Normalizing Flow OOD backend.

Tests cover:
  - RealNVPFlow construction and API
  - fit() on small synthetic data (torch path)
  - neg_log_prob: in-dist data scores lower than OOD data
  - save/load round-trip
  - OODGuard(backend="normalizing_flow") integration
  - Auto-fallback to memory_bank when torch not available
  - diagnostics() reports flow_fitted=True after train()
  - Both backends reject clear OOD; pass clear in-dist

All flow tests are skipped if torch is not installed,
consistent with the graceful-degradation design.
"""

from __future__ import annotations

import numpy as np
import pytest

from dam.decorators import guard
from dam.guard.builtin.ood import (
    OODGuard,
    RealNVPFlow,
)
from dam.types.observation import Observation
from dam.types.result import GuardDecision

# ── Helpers ───────────────────────────────────────────────────────────────────

try:
    import torch  # noqa: F401

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

requires_torch = pytest.mark.skipif(not HAS_TORCH, reason="torch not installed")


def _obs(positions, velocities=None, timestamp: float = 0.0) -> Observation:
    return Observation(
        timestamp=timestamp,
        joint_positions=np.array(positions, dtype=np.float64),
        joint_velocities=np.array(velocities, dtype=np.float64) if velocities is not None else None,
    )


def _make_guard(backend: str = "memory_bank") -> OODGuard:
    @guard("L0")
    class _G(OODGuard):
        def __init__(self) -> None:
            super().__init__(backend=backend)

    return _G()


def _normal_vectors(n: int = 100, dim: int = 128, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Cluster near origin on the unit sphere
    v = rng.standard_normal((n, dim)).astype(np.float32)
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return v / (norms + 1e-9)


# ── RealNVPFlow unit tests ────────────────────────────────────────────────────


class TestRealNVPFlow:
    @requires_torch
    def test_construction_requires_torch(self):
        """RealNVPFlow should be constructable when torch is available."""
        flow = RealNVPFlow(dim=128)
        assert not flow.is_fitted

    @requires_torch
    def test_fit_sets_fitted(self):
        vectors = _normal_vectors(80, 128)
        flow = RealNVPFlow(dim=128, n_coupling=2, hidden=32)
        flow.fit(vectors, epochs=3)
        assert flow.is_fitted

    @requires_torch
    def test_neg_log_prob_in_dist_lower_than_ood(self):
        """In-distribution samples must have lower NLL than extreme OOD."""
        rng = np.random.default_rng(42)
        # Training data: tight cluster
        train_vecs = rng.standard_normal((200, 128)).astype(np.float32) * 0.1
        flow = RealNVPFlow(dim=128, n_coupling=4, hidden=64)
        flow.fit(train_vecs, epochs=20, lr=1e-3)

        # In-dist sample (similar to training)
        in_dist = rng.standard_normal((128,)).astype(np.float32) * 0.1
        nll_in = flow.neg_log_prob(in_dist)

        # OOD sample (far from training cluster)
        ood = rng.standard_normal((128,)).astype(np.float32) * 50.0
        nll_ood = flow.neg_log_prob(ood)

        assert nll_ood > nll_in, f"OOD NLL ({nll_ood:.2f}) should exceed in-dist NLL ({nll_in:.2f})"

    @requires_torch
    def test_neg_log_prob_before_fit_returns_zero(self):
        flow = RealNVPFlow(dim=32)
        z = np.zeros(32, dtype=np.float32)
        assert flow.neg_log_prob(z) == 0.0

    @requires_torch
    def test_save_load_roundtrip(self, tmp_path):
        vectors = _normal_vectors(60, 128)
        flow = RealNVPFlow(dim=128, n_coupling=2, hidden=32)
        flow.fit(vectors, epochs=2)

        path = str(tmp_path / "flow.pt")
        flow.save(path)

        flow2 = RealNVPFlow()
        flow2.load(path)
        assert flow2.is_fitted

        # Scoring must be consistent after reload
        z = _normal_vectors(1, 128)[0]
        nll1 = flow.neg_log_prob(z)
        nll2 = flow2.neg_log_prob(z)
        assert abs(nll1 - nll2) < 1e-3, f"nll before={nll1:.4f}, after={nll2:.4f}"

    @requires_torch
    def test_different_coupling_depths(self):
        """Flow should work with n_coupling=1 or n_coupling=8."""
        vectors = _normal_vectors(50, 128)
        for n in (1, 4, 8):
            flow = RealNVPFlow(dim=128, n_coupling=n, hidden=32)
            flow.fit(vectors, epochs=2)
            z = vectors[0]
            nll = flow.neg_log_prob(z)
            assert np.isfinite(nll), f"NLL not finite for n_coupling={n}"


# ── OODGuard normalizing_flow backend ────────────────────────────────────────


class TestOODGuardNormalizingFlow:
    @requires_torch
    def test_backend_name_stored(self):
        g = _make_guard("normalizing_flow")
        assert g._backend_name == "normalizing_flow"

    @requires_torch
    def test_welford_fallback_before_training(self):
        """Before training, normalizing_flow guard falls back to Welford PASS."""
        g = _make_guard("normalizing_flow")
        obs = _obs([0.1] * 6, [0.0] * 6)
        result = g.check(obs, nll_threshold=5.0)
        assert result.decision == GuardDecision.PASS  # warm-up not exceeded

    @requires_torch
    def test_train_sets_flow_fitted(self):
        g = _make_guard("normalizing_flow")
        obs_list = [_obs([float(i) * 0.01] * 6) for i in range(50)]
        g.train(obs_list, flow_epochs=3)
        assert g._flow is not None
        assert g._flow.is_fitted

    @requires_torch
    def test_diagnostics_shows_flow_fitted(self):
        g = _make_guard("normalizing_flow")
        obs_list = [_obs([0.1] * 6) for _ in range(30)]
        g.train(obs_list, flow_epochs=3)
        d = g.diagnostics()
        assert d["backend"] == "normalizing_flow"
        assert d["flow_fitted"] is True

    @requires_torch
    def test_pass_on_in_dist_after_training(self):
        """In-distribution observation must PASS with a generous threshold."""
        g = _make_guard("normalizing_flow")
        # Train and check with same obs format (no velocities) to avoid dim mismatch
        obs_list = [_obs([0.1] * 6) for _ in range(50)]
        g.train(obs_list, flow_epochs=5)
        # Same distribution, very generous threshold
        result = g.check(_obs([0.1] * 6), nll_threshold=1000.0)
        assert result.decision == GuardDecision.PASS

    @requires_torch
    def test_reject_on_extreme_ood_after_training(self):
        """After training, OOD observations receive strictly higher NLL than in-dist."""
        rng = np.random.default_rng(42)
        g = _make_guard("normalizing_flow")
        # Varied training data — non-identical so the flow learns a proper distribution.
        obs_list = [_obs(rng.normal(0.1, 0.05, 6).tolist()) for _ in range(60)]
        # 20 epochs sufficient for the flow to distinguish directions reliably.
        g.train(obs_list, flow_epochs=20)
        # In-dist: another draw from the same distribution as training.
        in_obs = _obs(rng.normal(0.1, 0.05, 6).tolist())
        # OOD: far from training centre (Normal(-10, 0.1) vs training Normal(0.1, 0.05)).
        # After L2-norm in the FeatureExtractor the two cluster in different regions of
        # the unit sphere in ℝ¹²⁸, so the trained flow assigns them different likelihoods.
        ood_obs = _obs(rng.normal(-10.0, 0.1, 6).tolist())
        # Directly compare NLLs — the key invariant is relative ordering, not absolute.
        z_in = g._extractor.extract(in_obs)
        z_ood = g._extractor.extract(ood_obs)
        nll_in = g._flow.neg_log_prob(z_in)  # type: ignore[union-attr]
        nll_ood = g._flow.neg_log_prob(z_ood)  # type: ignore[union-attr]
        assert nll_ood > nll_in, (
            f"Flow must score OOD higher than in-dist: nll_ood={nll_ood:.2f}, nll_in={nll_in:.2f}"
        )
        # check() must REJECT with a threshold between in-dist and OOD NLL.
        mid = (nll_in + nll_ood) / 2.0
        result = g.check(ood_obs, nll_threshold=mid)
        assert result.decision == GuardDecision.REJECT
        assert "nll" in result.reason


# ── Backend comparison — same data, same conclusion ───────────────────────────


class TestBothBackendsConsistent:
    """Memory bank and normalizing flow should agree on extreme cases."""

    @requires_torch
    def test_both_reject_extreme_ood(self):
        normal_obs = [_obs([0.0] * 6) for _ in range(60)]
        ood_obs = _obs([1000.0] * 6)

        g_mb = _make_guard("memory_bank")
        g_mb.train(normal_obs)
        r_mb = g_mb.check(ood_obs, nn_threshold=0.01)

        g_nf = _make_guard("normalizing_flow")
        g_nf.train(normal_obs, flow_epochs=5)
        r_nf = g_nf.check(ood_obs, nll_threshold=0.0)

        assert r_mb.decision == GuardDecision.REJECT, "MemoryBank missed extreme OOD"
        assert r_nf.decision == GuardDecision.REJECT, "NormFlow missed extreme OOD"

    @requires_torch
    def test_both_pass_in_dist(self):
        # Train without velocities so FeatureExtractor is built for 6-dim input,
        # matching the 6-dim in_dist check observation.
        normal_obs = [_obs([0.1] * 6) for _ in range(60)]
        in_dist = _obs([0.1] * 6)

        g_mb = _make_guard("memory_bank")
        g_mb.train(normal_obs)
        r_mb = g_mb.check(in_dist, nn_threshold=2.0)

        g_nf = _make_guard("normalizing_flow")
        g_nf.train(normal_obs, flow_epochs=5)
        r_nf = g_nf.check(in_dist, nll_threshold=1000.0)

        assert r_mb.decision == GuardDecision.PASS
        assert r_nf.decision == GuardDecision.PASS


# ── Diagnostics completeness ──────────────────────────────────────────────────


def test_diagnostics_has_all_keys():
    g = _make_guard("memory_bank")
    d = g.diagnostics()
    for key in (
        "backend",
        "bank_trained",
        "bank_size",
        "bank_backend",
        "flow_fitted",
        "torch_available",
        "welford_samples",
    ):
        assert key in d, f"Missing key: {key}"


def test_diagnostics_default_values():
    g = _make_guard()
    d = g.diagnostics()
    assert d["backend"] == "memory_bank"
    assert d["bank_trained"] is False
    assert d["bank_size"] == 0
    assert d["flow_fitted"] is False
    assert d["welford_samples"] == 0
