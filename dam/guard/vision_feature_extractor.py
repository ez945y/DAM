"""Vision Feature Extractor — Pretrained HuggingFace model for image embeddings.

Extracts dense visual features from RGB camera frames using pretrained models
(MobileNetV3 or Theia). Used by L0 OOD guard to detect out-of-distribution
scenes that are indistinguishable from joint-position data alone.

Supported models:
    - mobilenet_v3_large  (960-dim, ~5M params, CPU-friendly)
    - mobilenet_v3_small  (576-dim, ~2.5M params, fastest)
    - theia-*             (192-dim, requires HF download)

Input:  torch.Tensor (N, H, W, C) uint8 [0-255] RGB
Output: torch.Tensor (N, embedding_dim) float32
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VisionFeatureExtractorConfig:
    model_name: str = "mobilenet_v3_large"
    device: str = "cpu"
    pool_output: bool = True


class VisionFeatureExtractor:
    """Extracts visual embeddings from RGB images using pretrained models."""

    def __init__(self, cfg: VisionFeatureExtractorConfig | None = None) -> None:
        if cfg is None:
            cfg = VisionFeatureExtractorConfig()
        self.cfg = cfg
        self._model: Any = None
        self._ready = False
        self._embedding_dim: int = 0

    @property
    def embedding_dim(self) -> int:
        if not self._ready:
            self._load_model()
        return self._embedding_dim

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _load_model(self) -> None:
        import torch

        device = torch.device(self.cfg.device)
        name = self.cfg.model_name

        if name == "mobilenet_v3_large":
            self._load_mobilenet_v3_large(device)
        elif name == "mobilenet_v3_small":
            self._load_mobilenet_v3_small(device)
        elif name.startswith("theia"):
            self._load_theia(device)
        else:
            raise ValueError(
                f"Unsupported vision model: {name}. "
                "Valid: mobilenet_v3_large, mobilenet_v3_small, theia-*"
            )
        self._ready = True
        logger.info(
            "VisionFeatureExtractor loaded: model=%s dim=%d device=%s",
            name,
            self._embedding_dim,
            self.cfg.device,
        )

    def _load_mobilenet_v3_large(self, device: Any) -> None:
        import torch
        import torch.nn.functional as F
        from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large

        weights = MobileNet_V3_Large_Weights.IMAGENET1K_V1
        model = mobilenet_v3_large(weights=weights).features.eval().to(device)
        self._embedding_dim = 960
        self._device = device
        self._model = model
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)

    def _load_mobilenet_v3_small(self, device: Any) -> None:
        import torch
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1
        model = mobilenet_v3_small(weights=weights).features.eval().to(device)
        self._embedding_dim = 576
        self._device = device
        self._model = model
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)

    def _load_theia(self, device: Any) -> None:
        import torch
        from transformers import AutoModel

        model = (
            AutoModel.from_pretrained(
                f"theaiinstitute/{self.cfg.model_name}", trust_remote_code=True
            )
            .eval()
            .to(device)
        )
        self._embedding_dim = 192
        self._device = device
        self._model = model
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
        self._is_theia = True

    def extract(self, images: np.ndarray) -> np.ndarray:
        """Extract features from RGB images.

        Args:
            images: (N, H, W, 3) uint8 RGB array.

        Returns:
            (N, embedding_dim) float32 feature vectors.
        """
        if not self._ready:
            self._load_model()

        import torch
        import torch.nn.functional as F

        img_t = torch.from_numpy(images).to(self._device).permute(0, 3, 1, 2).float() / 255.0
        img_t = (img_t - self._mean) / self._std

        with torch.no_grad():
            if hasattr(self, "_is_theia") and self._is_theia:
                features = self._model.backbone.model(
                    pixel_values=img_t, interpolate_pos_encoding=True
                )
                out = features.last_hidden_state[:, 1:].mean(dim=1)
            else:
                features = self._model(img_t)
                out = F.adaptive_avg_pool2d(features, (1, 1)).flatten(start_dim=1)

        return np.asarray(out.cpu().numpy(), dtype=np.float32)

    def extract_single(self, image: np.ndarray) -> np.ndarray:
        """Extract features from a single (H, W, 3) uint8 RGB image."""
        result: np.ndarray = self.extract(image[np.newaxis])[0]
        return result
