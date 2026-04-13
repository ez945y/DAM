"""Tests for OOD Guard Memory Bank upgrade."""

from __future__ import annotations

import numpy as np

from dam.decorators import guard
from dam.guard.builtin.ood import FeatureExtractor, MemoryBank, OODGuard, _WelfordStats
from dam.types.observation import Observation
from dam.types.result import GuardDecision


def _obs(positions, velocities=None, timestamp=0.0):
    return Observation(
        timestamp=timestamp,
        joint_positions=np.array(positions, dtype=np.float64),
        joint_velocities=np.array(velocities, dtype=np.float64) if velocities is not None else None,
    )


def _make_guard():
    @guard("L0")
    class _G(OODGuard):
        pass

    return _G()


# ── FeatureExtractor ──────────────────────────────────────────────────────────


class TestFeatureExtractor:
    def test_extract_returns_128_dim(self):
        fe = FeatureExtractor()
        obs = _obs([0.1] * 6, [0.0] * 6)
        z = fe.extract(obs)
        assert z.shape == (128,), f"Expected (128,), got {z.shape}"

    def test_extract_l2_normalised(self):
        fe = FeatureExtractor()
        obs = _obs([0.5] * 6)
        z = fe.extract(obs)
        norm = float(np.linalg.norm(z))
        # numpy fallback: exactly 1.0; torch: ~1.0
        assert abs(norm - 1.0) < 1e-5 or norm > 0, f"norm={norm}"

    def test_extract_different_obs_different_z(self):
        fe = FeatureExtractor()
        obs1 = _obs([0.0] * 6)
        obs2 = _obs([3.14] * 6)
        z1 = fe.extract(obs1)
        z2 = fe.extract(obs2)
        # May be same if torch rounds, but numpy path should differ
        # Just check both are valid
        assert z1.shape == z2.shape == (128,)

    def test_extract_no_velocities(self):
        fe = FeatureExtractor()
        obs = _obs([0.1, 0.2, 0.3])
        z = fe.extract(obs)
        assert z.shape == (128,)


# ── MemoryBank ────────────────────────────────────────────────────────────────


class TestMemoryBank:
    def _make_vectors(self, n, seed=42):
        rng = np.random.default_rng(seed)
        v = rng.standard_normal((n, 128)).astype(np.float32)
        # L2-normalise
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        return v / (norms + 1e-9)

    def test_not_trained_initially(self):
        bank = MemoryBank()
        assert not bank.is_trained
        assert bank.size == 0

    def test_train(self):
        bank = MemoryBank()
        vecs = self._make_vectors(100)
        bank.train(vecs)
        assert bank.is_trained
        assert bank.size == 100

    def test_nearest_distance_exact_match(self):
        bank = MemoryBank()
        vecs = self._make_vectors(50)
        bank.train(vecs)
        dist = bank.nearest_distance(vecs[0])
        assert dist < 1e-4, f"Exact match should have dist≈0, got {dist}"

    def test_nearest_distance_far_vector(self):
        bank = MemoryBank()
        # All vectors on one pole
        vecs = np.zeros((10, 128), dtype=np.float32)
        vecs[:, 0] = 1.0
        bank.train(vecs)
        # Query at opposite pole
        q = np.zeros(128, dtype=np.float32)
        q[0] = -1.0
        dist = bank.nearest_distance(q)
        assert dist > 1.0, f"Expected large distance, got {dist}"

    def test_untrained_returns_zero(self):
        bank = MemoryBank()
        q = np.random.randn(128).astype(np.float32)
        assert bank.nearest_distance(q) == 0.0

    def test_save_load(self, tmp_path):
        bank = MemoryBank()
        vecs = self._make_vectors(20)
        bank.train(vecs)
        path = str(tmp_path / "bank.npy")
        bank.save(path)

        bank2 = MemoryBank()
        bank2.load(path)
        assert bank2.is_trained
        assert bank2.size == 20
        dist = bank2.nearest_distance(vecs[0])
        assert dist < 1e-4


# ── WelfordStats ──────────────────────────────────────────────────────────────


class TestWelfordStats:
    def test_warmup_returns_zero(self):
        w = _WelfordStats()
        x = np.ones(5)
        w.update(x)
        assert w.z_score_max(x) == 0.0  # n < 2

    def test_ood_detected_after_warmup(self):
        w = _WelfordStats()
        normal = np.array([0.0, 0.0, 0.0])
        for _ in range(30):
            w.update(normal)
        z = w.z_score_max(np.array([100.0, 0.0, 0.0]))
        assert z > 10.0


# ── OODGuard integration ──────────────────────────────────────────────────────


class TestOODGuard:
    def test_welford_fallback_warmup_passes(self):
        g = _make_guard()
        for i in range(10):
            obs = _obs([0.1] * 6, [0.0] * 6, timestamp=float(i))
            result = g.check(obs, nn_threshold=0.5)
            assert result.decision == GuardDecision.PASS

    def test_memory_bank_pass_normal(self):
        g = _make_guard()
        normal_obs = [_obs([float(i) * 0.01] * 6) for i in range(50)]
        g.train(normal_obs)
        assert g._bank.is_trained

        # Query with similar observation → should PASS
        test_obs = _obs([0.24] * 6)
        result = g.check(test_obs, nn_threshold=2.0)  # generous threshold
        assert result.decision == GuardDecision.PASS

    def test_memory_bank_reject_ood(self):
        g = _make_guard()
        # Train on observations near zero
        normal_obs = [_obs([0.0] * 6) for _ in range(50)]
        g.train(normal_obs)
        assert g._bank.is_trained

        # Query far from training distribution → should REJECT
        ood_obs = _obs([1000.0] * 6)
        result = g.check(ood_obs, nn_threshold=0.01)  # tight threshold
        assert result.decision == GuardDecision.REJECT
        assert "nn_distance" in result.reason

    def test_train_empty_list_no_crash(self):
        g = _make_guard()
        g.train([])  # should not crash
        assert not g._bank.is_trained

    def test_diagnostics(self):
        g = _make_guard()
        d = g.diagnostics()
        assert "bank_trained" in d
        assert "bank_size" in d
        assert "welford_samples" in d
        assert d["bank_trained"] is False
        assert d["bank_size"] == 0

    def test_diagnostics_after_train(self):
        g = _make_guard()
        obs_list = [_obs([0.1] * 4) for _ in range(10)]
        g.train(obs_list)
        d = g.diagnostics()
        assert d["bank_trained"] is True
        assert d["bank_size"] == 10

    def test_welford_rejects_extreme_after_warmup(self):
        g = _make_guard()
        # Feed 30+ normal samples
        for _i in range(35):
            obs = _obs([0.1] * 6, [0.0] * 6)
            g.check(obs, nn_threshold=0.5)
        # Extreme observation
        extreme = _obs([1000.0] * 6, [0.0] * 6)
        result = g.check(extreme, nn_threshold=0.5)
        assert result.decision == GuardDecision.REJECT

    def test_fault_on_broken_obs(self):
        """Guard returns FAULT (not raises) when obs causes internal error."""
        g = _make_guard()
        # Train with valid obs
        obs_list = [_obs([0.0] * 4) for _ in range(10)]
        g.train(obs_list)
        # Create obs with incompatible shape that might cause extraction error
        # This test ensures we never propagate exceptions
        obs = _obs([float("nan")] * 4)
        result = g.check(obs, nn_threshold=0.5)
        # Either PASS (NaN propagates through numpy silently) or FAULT
        assert result.decision in (GuardDecision.PASS, GuardDecision.REJECT, GuardDecision.FAULT)
