"""L0 out-of-distribution boundary callback.

Stackfiles expose one OOD boundary, ``ood_detector``.  The ``backend`` param
selects the scoring strategy (Real-NVP, memory bank, or online Welford) while
the callback keeps one shared feature/context path for deployment and RQ1
runtime export.

All backends share a single ``sigma`` sensitivity parameter: how many standard
deviations from the training distribution a score may drift before it is
flagged as OOD.  When a calibrated threshold is embedded in the model bundle
(RQ1 / EER), it overrides the sigma-derived value automatically.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.ood_backend import OODBackendKind
from dam.guard.ood_context import OODContext
from dam.guard.pipeline import CallbackResult
from dam.types.observation import Observation

_DEFAULT_SIGMA = 3.0
_logger = logging.getLogger(__name__)


def _key(name: str, model_path: str, bank_path: str, backend: str) -> str:
    return f"{name}|{backend}|{model_path}|{bank_path}"


def _load_calibrated_threshold(model_path: str) -> float | None:
    """Read RQ1/EER calibrated threshold from the model bundle metadata."""
    meta_path = Path(model_path).with_suffix(".json")
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text())
        sp = meta.get("stackfile_params", {})
        if sp.get("nll_sigma", -1) == 0 and "nll_threshold" in sp:
            return float(sp["nll_threshold"])
    except Exception:  # noqa: BLE001
        pass
    return None


def _welford_verdict(
    *,
    bname: str,
    ood_context: OODContext,
    obs: Observation,
    key: str,
    sigma: float,
    warmup: int,
) -> CallbackResult:
    w = ood_context.get_backend(kind=OODBackendKind.WELFORD, key=key, warmup=warmup)
    raw = ood_context.raw_features(obs)
    score = w.score(raw)
    threshold = w.threshold(sigma)  # type: ignore[attr-defined]
    in_warmup = w.in_warmup  # type: ignore[attr-defined]
    meta: dict[str, Any] = {
        "score": score,
        "threshold": threshold,
        "backend": "welford",
        "warmup": in_warmup,
    }
    if not in_warmup and score > threshold:
        return CallbackResult.violate(
            bname,
            f"OOD z-score={score:.2f} > threshold={threshold:.2f} (Welford)",
            metadata=meta,
        )
    w.observe(raw)  # type: ignore[attr-defined]
    return CallbackResult.ok(bname, metadata=meta)


@boundary_callback(
    name="ood_detector",
    layer="L0",
    category="anomaly",
    description="Out-of-distribution boundary with a selectable scoring backend.",
    params={
        "backend": "OOD backend: normalizing_flow (Real-NVP), memory_bank, or welford.",
        "ood_model_path": "Path to the trained OOD feature/model artifact.",
        "bank_path": "Path to memory-bank calibration vectors when using memory_bank.",
        "sigma": "Unified sensitivity: standard deviations from training mean before OOD rejection.",
        "device": "Device used by the OOD backend, such as cpu, cuda, or mps.",
        "warmup": "Number of Welford warm-up observations before online rejection.",
        "vision_model": "Optional pretrained vision model fused with robot-state features.",
        "vision_weight": "Weight of vision features in the fused embedding.",
        "vision_camera": "Camera image key used for pretrained vision features.",
        "temporal_smoothing_frames": "Consecutive OOD frames required before rejecting.",
    },
)
def ood_detector(
    *,
    obs: Observation,
    ood_context: OODContext,
    backend: str = "normalizing_flow",
    ood_model_path: str = "",
    bank_path: str = "",
    sigma: float = _DEFAULT_SIGMA,
    # Legacy params — migrated to sigma; kept for stackfile backward compat.
    nn_threshold: float | None = None,
    z_threshold: float | None = None,
    nll_sigma: float | None = None,
    nll_threshold: float | None = None,
    device: str = "cpu",
    warmup: int = 30,
    vision_model: str = "",
    vision_weight: float = 0.3,
    vision_camera: str = "",
    temporal_smoothing_frames: int = 3,
) -> CallbackResult:
    """Score one observation using the configured OOD backend."""
    del temporal_smoothing_frames  # Applied by OODGuard after callback aggregation.

    # Resolve effective sigma from legacy params when sigma is at default.
    effective_sigma = sigma
    if sigma == _DEFAULT_SIGMA:
        if nll_sigma is not None and nll_sigma > 0:
            effective_sigma = nll_sigma
        elif z_threshold is not None:
            effective_sigma = z_threshold

    bname = "ood_detector"
    try:
        kind = OODBackendKind.from_value(backend)
        if kind is OODBackendKind.WELFORD:
            return _welford_verdict(
                bname=bname,
                ood_context=ood_context,
                obs=obs,
                key=_key(bname, ood_model_path, bank_path, kind.value),
                sigma=effective_sigma,
                warmup=warmup,
            )

        if vision_model:
            ood_context.configure_vision(
                vision_model, vision_weight, device, vision_camera=vision_camera or None
            )
        key = _key(bname, ood_model_path, bank_path, kind.value)
        detector = ood_context.get_backend(kind=kind, key=key, device=device)
        ood_context.load_backend(
            detector,
            key=key,
            obs=obs,
            model_path=ood_model_path or None,
            bank_path=bank_path or None,
            device=device,
        )
        if not detector.is_ready():
            return _welford_verdict(
                bname=bname,
                ood_context=ood_context,
                obs=obs,
                key=f"{key}::welford",
                sigma=effective_sigma,
                warmup=warmup,
            )

        score = detector.score(ood_context.features(obs))

        # Calibrated threshold from RQ1 bundle overrides sigma-derived value.
        calibrated = None
        if kind is OODBackendKind.NORMALIZING_FLOW and ood_model_path:
            if nll_sigma is not None and nll_sigma <= 0 and nll_threshold is not None:
                calibrated = nll_threshold
            else:
                calibrated = _load_calibrated_threshold(ood_model_path)

        if kind is OODBackendKind.MEMORY_BANK:
            if nn_threshold is not None:
                threshold = nn_threshold
            else:
                threshold = detector.threshold(effective_sigma)  # type: ignore[attr-defined]
        else:
            threshold = detector.threshold(  # type: ignore[attr-defined]
                effective_sigma, calibrated_threshold=calibrated
            )

        reason = f"OOD score={score:.4f} > threshold={threshold:.4f} ({kind.value})"
        meta: dict[str, Any] = {
            "score": score,
            "threshold": threshold,
            "backend": kind.value,
        }
        if calibrated is not None:
            meta["calibrated"] = True
        if score > threshold:
            return CallbackResult.violate(bname, reason, metadata=meta)
        return CallbackResult.ok(bname, metadata=meta)
    except Exception as exc:  # noqa: BLE001
        return CallbackResult.fault(bname, exc, fault_source="ood_backend")


__all__ = ["ood_detector", "_key"]
