"""Tests for the L0 OOD boundary callbacks + OODGuard's pipeline path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from dam.boundary.callbacks.ood import _key, ood_detector
from dam.guard.builtin.ood import OODGuard
from dam.guard.ood_context import OODContext
from dam.types.observation import Observation
from dam.types.result import GuardDecision

_EMBED_DIM = 128


def _obs(positions=None) -> Observation:
    return Observation(
        timestamp=0.0,
        joint_positions=np.zeros(6) if positions is None else np.array(positions, dtype=float),
        end_effector_pose=np.zeros(7),
    )


# ── stub container for the pipeline path ──────────────────────────────────────


@dataclass
class _Constraint:
    callback: str | None
    params: dict[str, Any]


@dataclass
class _Node:
    node_id: str
    constraint: _Constraint | None


class _Container:
    def __init__(self, name, callback, params=None):
        self.name = name
        self._node = _Node(f"{name}/0", _Constraint(callback, params or {}))

    def get_active_node(self):
        return self._node


# ── unified OOD callback ──────────────────────────────────────────────────────


def test_ood_detector_welford_backend_warmup_then_reject() -> None:
    ctx = OODContext.default()
    for _ in range(30):
        r = ood_detector(obs=_obs([0.01] * 6), ood_context=ctx, backend="welford")
        assert r.decision == GuardDecision.PASS
        assert r.metadata["backend"] == "welford"
    r = ood_detector(obs=_obs([100.0] * 6), ood_context=ctx, backend="welford")
    assert r.decision == GuardDecision.REJECT
    assert "score" in r.metadata and "threshold" in r.metadata


def test_ood_detector_memory_bank_falls_back_to_welford_when_untrained() -> None:
    ctx = OODContext.default()
    # No trained bank → behaves like Welford warm-up (PASS during warm-up).
    r = ood_detector(obs=_obs([0.1] * 6), ood_context=ctx, backend="memory_bank", nn_threshold=2.0)
    assert r.decision == GuardDecision.PASS
    assert r.metadata["backend"] == "welford"


def test_ood_detector_memory_bank_uses_bank_when_trained() -> None:
    ctx = OODContext.default()
    # Train the bank under the same key the callback resolves to (no path args).
    key = _key("ood_detector", "", "", "memory_bank")
    backend = ctx.get_backend(kind="memory_bank", key=key)
    rng = np.random.RandomState(0)
    vectors = rng.randn(40, _EMBED_DIM).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    backend.train(vectors)

    r = ood_detector(obs=_obs([0.1] * 6), ood_context=ctx, backend="memory_bank", nn_threshold=10.0)
    assert r.decision == GuardDecision.PASS
    assert r.metadata["backend"] == "memory_bank"
    # An impossibly tight threshold flags everything as OOD.
    r2 = ood_detector(
        obs=_obs([0.1] * 6), ood_context=ctx, backend="memory_bank", nn_threshold=-1.0
    )
    assert r2.decision == GuardDecision.REJECT


def test_builtin_callbacks_exports_l0_ood_callbacks() -> None:
    from dam.boundary import builtin_callbacks

    assert builtin_callbacks.ood_detector is ood_detector


# ── OODGuard pipeline path ────────────────────────────────────────────────────


def test_ood_guard_runs_l0_callback_pipeline() -> None:
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = OODGuard()
    container = _Container("ood", callback="ood_detector", params={"backend": "welford"})
    # Warm-up frames pass through the callback pipeline.
    for _ in range(30):
        result = g.check(obs=_obs([0.01] * 6), active_containers=[container])
        assert result.decision == GuardDecision.PASS
    result = g.check(obs=_obs([100.0] * 6), active_containers=[container])
    assert result.decision == GuardDecision.REJECT


def test_ood_guard_synthesizes_default_container_without_l0_callback() -> None:
    """No active containers → check() synthesizes a default SingleNodeContainer
    wired to ood_detector with the guard backend (memory_bank falls back to
    welford warm-up when untrained)."""
    g = OODGuard()
    for _ in range(30):
        assert g.check(obs=_obs([0.01] * 6)).decision == GuardDecision.PASS
    assert g.check(obs=_obs([100.0] * 6)).decision == GuardDecision.REJECT


def test_ood_guard_default_path_handles_missing_model_files() -> None:
    """The synthesized default container falls back to welford warm-up when
    the configured model/bank files don't exist on disk — no crash, no
    spurious REJECT, and no in-line lazy load on the hot path."""
    g = OODGuard()
    result = g.check(
        obs=_obs([0.01] * 6),
        ood_model_path="/tmp/missing-model.pt",
        bank_path="/tmp/missing-bank.npz",
    )
    assert result.decision == GuardDecision.PASS
    # Welford under the path-keyed fallback inside the configured detector.
    assert any(
        backend.diagnostics().get("welford_samples", 0) >= 1
        for backend in g._ood_context._backends.values()
    )


def test_ood_guard_pipeline_respects_temporal_smoothing() -> None:
    from dam.boundary.builtin_callbacks import register_all

    register_all()
    g = OODGuard()
    container = _Container("ood", callback="ood_detector", params={"backend": "welford"})
    for _ in range(30):
        g.check(obs=_obs([0.01] * 6), active_containers=[container])
    a = g.check(obs=_obs([100.0] * 6), active_containers=[container], temporal_smoothing_frames=2)
    b = g.check(obs=_obs([100.0] * 6), active_containers=[container], temporal_smoothing_frames=2)
    assert a.decision == GuardDecision.PASS  # suppressed by smoothing
    assert b.decision == GuardDecision.REJECT
