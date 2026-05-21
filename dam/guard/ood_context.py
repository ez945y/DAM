"""Shared state for L0 OOD detection — feature extractor + backend cache.

The L0 OOD callbacks are stateless functions, but OOD detection needs durable
state: the feature extractor (and its loaded weights) and the per-algorithm
backends (a trained MemoryBank, a fitted flow, a Welford running stat).
``OODContext`` owns that state and is injected into the callbacks via the
runtime pool, so the callbacks themselves never touch model lifecycle.

Model loading is done **once** per ``(key, path)`` and cached, keeping disk IO
out of the per-cycle hot path.  (A future Phase 6.1 can hoist the first load
into ``GuardRuntime`` preflight; for now the first ``load_backend`` call does
it and subsequent cycles hit the cache.)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from dam.guard.ood_backend import OODBackend, OODBackendKind, make_backend
from dam.types.observation import Observation

if TYPE_CHECKING:
    from dam.guard.builtin.ood import FeatureExtractor

logger = logging.getLogger(__name__)

_FLOW_SUFFIX = "_flow.pt"


class OODContext:
    """Holds the feature extractor and a cache of OOD backends keyed by string."""

    def __init__(self, feature_extractor: FeatureExtractor | None = None) -> None:
        from dam.guard.builtin.ood import FeatureExtractor

        self.feature_extractor: FeatureExtractor = feature_extractor or FeatureExtractor()
        self._backends: dict[str, OODBackend] = {}
        self._loaded: dict[str, str] = {}  # key -> path marker already loaded
        self._extractor_loaded_from: str | None = None

    @classmethod
    def default(cls) -> OODContext:
        return cls()

    # ── Features ──────────────────────────────────────────────────────────────

    def features(self, obs: Observation) -> np.ndarray:
        """128-dim embedding for the bank / flow backends."""
        return self.feature_extractor.extract(obs)

    @staticmethod
    def raw_features(obs: Observation) -> np.ndarray:
        """Raw joint vector for the Welford backend (matches legacy behaviour)."""
        return np.asarray(obs.joint_positions, dtype=np.float64)

    # ── Backend cache ────────────────────────────────────────────────────────

    def get_backend(
        self,
        *,
        kind: OODBackendKind | str,
        key: str,
        device: str = "cpu",
        **make_kwargs: Any,
    ) -> OODBackend:
        """Return the cached backend for ``key``, constructing it on first use."""
        if key not in self._backends:
            self._backends[key] = make_backend(kind, device=device, **make_kwargs)
        return self._backends[key]

    def ensure_extractor(
        self, obs: Observation, model_path: str | None, device: str = "cpu"
    ) -> None:
        """Load the feature-extractor weights once (no-op if already loaded)."""
        if model_path and model_path != self._extractor_loaded_from and Path(model_path).exists():
            self.feature_extractor.load(
                model_path,
                int(obs.joint_positions.shape[0]),
                bool(obs.images),
                device=device,
            )
            self._extractor_loaded_from = model_path

    def load_backend(
        self,
        backend: OODBackend,
        *,
        key: str,
        obs: Observation,
        model_path: str | None = None,
        bank_path: str | None = None,
        device: str = "cpu",
    ) -> None:
        """Load a backend's weights from disk once per ``(key, path)``."""
        try:
            if backend.kind is OODBackendKind.MEMORY_BANK and bank_path:
                marker = f"bank:{bank_path}"
                if self._loaded.get(key) != marker and Path(bank_path).exists():
                    self.ensure_extractor(obs, model_path, device)
                    backend.load(bank_path)
                    self._loaded[key] = marker
            elif backend.kind is OODBackendKind.NORMALIZING_FLOW and model_path:
                flow_path = model_path.replace(".pt", _FLOW_SUFFIX)
                marker = f"flow:{flow_path}"
                if self._loaded.get(key) != marker and Path(flow_path).exists():
                    self.ensure_extractor(obs, model_path, device)
                    backend.load(flow_path)
                    self._loaded[key] = marker
        except Exception:  # noqa: BLE001 — missing/corrupt model → run untrained
            logger.exception("OODContext: backend load failed for key=%s", key)

    def diagnostics(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "torch_available": getattr(self.feature_extractor, "_torch_available", False),
            "n_backends": len(self._backends),
        }
        for key, backend in self._backends.items():
            out[key] = backend.diagnostics()
        return out
