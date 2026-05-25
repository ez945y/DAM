"""Unit tests for the OOD backend protocol + adapters (dam/guard/ood_backend.py)."""

from __future__ import annotations

import numpy as np
import pytest

from dam.guard.ood_backend import (
    MemoryBankBackend,
    OODBackend,
    OODBackendKind,
    RealNVPFlowBackend,
    WelfordBackend,
    make_backend,
)

_EMBED_DIM = 128


# ── OODBackendKind ────────────────────────────────────────────────────────────


def test_kind_from_canonical_strings() -> None:
    assert OODBackendKind.from_value("welford") is OODBackendKind.WELFORD
    assert OODBackendKind.from_value("memory_bank") is OODBackendKind.MEMORY_BANK
    assert OODBackendKind.from_value("normalizing_flow") is OODBackendKind.NORMALIZING_FLOW


def test_kind_from_aliases_and_enum() -> None:
    assert OODBackendKind.from_value("flow") is OODBackendKind.NORMALIZING_FLOW
    assert OODBackendKind.from_value("KNN") is OODBackendKind.MEMORY_BANK
    assert OODBackendKind.from_value(OODBackendKind.WELFORD) is OODBackendKind.WELFORD


def test_kind_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown OOD backend"):
        OODBackendKind.from_value("bogus")


def test_make_backend_returns_protocol_instances() -> None:
    for kind in ("welford", "memory_bank"):
        b = make_backend(kind)
        assert isinstance(b, OODBackend)
        assert b.kind is OODBackendKind.from_value(kind)


# ── Welford ───────────────────────────────────────────────────────────────────


def test_welford_warmup_then_scores() -> None:
    b = WelfordBackend(warmup=30)
    feat = np.array([0.1] * 6)
    for _ in range(30):
        assert b.in_warmup
        assert b.score(feat) == 0.0  # warm-up always in-dist
        b.observe(feat)
    assert not b.in_warmup
    # After warm-up, a sample matching the baseline scores ~0; an outlier scores high.
    assert b.score(np.array([0.1] * 6)) < 1.0
    assert b.score(np.array([100.0] * 6)) > 5.0


def test_welford_is_always_ready_and_diagnostics() -> None:
    b = WelfordBackend()
    assert b.is_ready()
    b.observe(np.zeros(6))
    assert b.diagnostics()["welford_samples"] == 1


def test_welford_save_load_roundtrip(tmp_path) -> None:
    b = WelfordBackend(warmup=2)
    for _ in range(5):
        b.observe(np.array([1.0, 2.0, 3.0]))
    p = tmp_path / "welford.npz"
    b.save(p)
    b2 = WelfordBackend(warmup=2)
    b2.load(p)
    assert b2.diagnostics()["welford_samples"] == 5


# ── MemoryBank ────────────────────────────────────────────────────────────────


def test_memory_bank_train_score_ready() -> None:
    b = MemoryBankBackend()
    assert not b.is_ready()
    vectors = np.random.RandomState(0).randn(50, _EMBED_DIM).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    b.train(vectors)
    assert b.is_ready()
    # A point in the bank scores ~0; a far point scores larger.
    near = b.score(vectors[0])
    far = b.score(-vectors[0])
    assert near < far
    assert b.diagnostics()["bank_size"] == 50
    # Training computes distance statistics for unified sigma threshold.
    assert b.mean_train_dist is not None
    assert b.std_train_dist is not None
    assert b.threshold(3.0) == pytest.approx(b.mean_train_dist + 3.0 * b.std_train_dist)


def test_memory_bank_scores_fused_vision_embeddings() -> None:
    rng = np.random.RandomState(1)
    vectors = rng.randn(20, 256).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    b = MemoryBankBackend()

    b.train(vectors)

    assert b.score(vectors[0]) < 1e-4
    assert b.diagnostics()["bank_dim"] == 256


# ── RealNVP flow ──────────────────────────────────────────────────────────────


def test_flow_backend_unfitted_score_zero_not_ready() -> None:
    # No torch needed: an unfitted flow backend reports not-ready, score 0.
    b = RealNVPFlowBackend()
    assert not b.is_ready()
    assert b.score(np.zeros(_EMBED_DIM)) == 0.0


def test_flow_backend_threshold_prefers_training_stats() -> None:
    b = RealNVPFlowBackend()
    # No training stats → sigma used as raw fallback.
    assert b.threshold(3.0) == 3.0
    # Calibrated threshold overrides everything.
    assert b.threshold(3.0, calibrated_threshold=5.0) == 5.0
    b.mean_train_nll = 10.0
    b.std_train_nll = 2.0
    # With training stats: mean + sigma * std.
    assert b.threshold(3.0) == pytest.approx(16.0)
    # Calibrated still wins even with training stats.
    assert b.threshold(3.0, calibrated_threshold=5.0) == 5.0


def test_flow_backend_train_and_score(tmp_path) -> None:
    pytest.importorskip("torch")
    rng = np.random.RandomState(0)
    vectors = rng.randn(64, _EMBED_DIM).astype(np.float32)
    b = RealNVPFlowBackend()
    b.train(vectors, epochs=3)
    assert b.is_ready()
    assert b.mean_train_nll is not None
    # save/load roundtrip restores fitted state + stats
    p = tmp_path / "flow.pt"
    b.save(p)
    b2 = RealNVPFlowBackend()
    b2.load(p)
    assert b2.is_ready()
    assert b2.mean_train_nll == pytest.approx(b.mean_train_nll, rel=1e-5)
