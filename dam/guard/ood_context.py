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
    from dam.guard.vision_feature_extractor import VisionFeatureExtractor

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
        self._vision_extractor: VisionFeatureExtractor | None = None
        self._vision_model_name: str | None = None
        self._vision_weight: float = 0.3
        self._vision_pca: Any | None = None  # (mean, components) for projecting vision features
        self._vision_dim: int = 128

    @classmethod
    def default(cls) -> OODContext:
        return cls()

    # ── Vision Feature Extractor ─────────────────────────────────────────────

    def configure_vision(
        self,
        vision_model: str | None = None,
        vision_weight: float = 0.3,
        device: str = "cpu",
    ) -> None:
        """Configure the optional vision feature extractor.

        When set, ``features()`` returns a fused vector: joint + vision embeddings
        weighted by ``vision_weight``.  When None, behaviour is unchanged.
        """
        if not vision_model:
            self._vision_extractor = None
            self._vision_model_name = None
            return
        if vision_model == self._vision_model_name and self._vision_extractor is not None:
            self._vision_weight = vision_weight
            return
        from dam.guard.vision_feature_extractor import (
            VisionFeatureExtractor,
            VisionFeatureExtractorConfig,
        )

        cfg = VisionFeatureExtractorConfig(model_name=vision_model, device=device)
        self._vision_extractor = VisionFeatureExtractor(cfg)
        self._vision_model_name = vision_model
        self._vision_weight = max(0.0, min(1.0, vision_weight))
        logger.info(
            "OODContext: vision extractor configured: model=%s weight=%.2f",
            vision_model,
            self._vision_weight,
        )

    def set_vision_pca(self, mean: np.ndarray, components: np.ndarray) -> None:
        """Set PCA projection for reducing vision features to target dim."""
        self._vision_pca = (mean.astype(np.float32), components.astype(np.float32))
        self._vision_dim = components.shape[0]

    # ── Features ──────────────────────────────────────────────────────────────

    def features(self, obs: Observation) -> np.ndarray:
        """Embedding vector for the bank / flow backends.

        Without vision: 128-dim joint embedding (existing behaviour).
        With vision: fused vector combining joint and vision embeddings (256-dim).
        When vision is configured but no image is available for this frame,
        pads with zeros to maintain consistent dimensionality.
        """
        joint_z = self.feature_extractor.extract(obs)

        if self._vision_extractor is None:
            return joint_z

        vision_z = self._extract_vision(obs)

        alpha = self._vision_weight
        joint_norm = np.linalg.norm(joint_z)
        if joint_norm > 1e-9:
            joint_z = joint_z / joint_norm

        if vision_z is None:
            vision_z = np.zeros(128, dtype=np.float32)
        else:
            vision_norm = np.linalg.norm(vision_z)
            if vision_norm > 1e-9:
                vision_z = vision_z / vision_norm

        fused: np.ndarray = np.concatenate(
            [
                joint_z * (1.0 - alpha),
                vision_z * alpha,
            ]
        ).astype(np.float32)
        norm = float(np.linalg.norm(fused))
        if norm > 1e-9:
            fused = fused / norm
        return fused

    def _extract_vision(self, obs: Observation) -> np.ndarray | None:
        """Extract and project vision features from the first available camera.

        Always returns a 128-dim vector (matching joint embedding dim) so the
        fused output has consistent dimensionality.
        """
        if self._vision_extractor is None or obs.images is None:
            return None
        cam_img = next(iter(obs.images.values()), None)
        if cam_img is None:
            return None
        raw_feat = self._vision_extractor.extract_single(cam_img)
        if self._vision_pca is not None:
            mean, components = self._vision_pca
            raw_feat = ((raw_feat - mean) @ components.T).astype(np.float32)
        target = 128
        if len(raw_feat) > target:
            return raw_feat[:target]
        elif len(raw_feat) < target:
            return np.pad(raw_feat, (0, target - len(raw_feat)))
        return raw_feat

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
