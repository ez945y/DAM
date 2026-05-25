"""L0 — Out-of-distribution boundary callbacks.

One callback per OOD algorithm.  A stackfile selects the detector the same way
it selects any other boundary behaviour — by naming the callback::

    boundaries:
      ood:
        layer: L0
        nodes:
          - callback: ood_memory_bank
            params: {nn_threshold: 2.0, ood_model_path: ..., bank_path: ...}

All three return a ``CallbackResult`` (PASS / REJECT / FAULT) and read their
durable state (feature extractor, trained backend) from the injected
``ood_context``; they never touch model lifecycle directly.  When the chosen
backend is not yet trained, they degrade to the Welford warm-up detector — the
same auto-fallback the monolithic ``OODGuard`` used to do.

Temporal smoothing (requiring N consecutive OOD frames before REJECT) is *not*
done here — it is cross-frame guard state and lives in ``OODGuard``.

Note on metadata: ``aggregate()`` only folds per-boundary ``metadata`` into the
final ``GuardResult`` for FAULT/REJECT/CLAMP, not PASS.  So the ``score`` /
``threshold`` attached below surface on a REJECT but are dropped on a PASS — by
design (a passing OOD check needs no audit payload); don't rely on PASS scores
appearing in ``GuardResult.metadata``.
"""

from __future__ import annotations

from typing import Any

from dam.boundary.callbacks._registry import boundary_callback
from dam.guard.ood_backend import OODBackendKind
from dam.guard.ood_context import OODContext
from dam.guard.pipeline import CallbackResult
from dam.types.observation import Observation

_WELFORD_Z_THRESHOLD = 5.0


def _key(name: str, model_path: str, bank_path: str, backend: str) -> str:
    return f"{name}|{backend}|{model_path}|{bank_path}"


def _welford_verdict(
    *,
    bname: str,
    ood_context: OODContext,
    obs: Observation,
    key: str,
    z_threshold: float,
    warmup: int,
) -> CallbackResult:
    """Online z-score path (also the fallback when a trained backend is absent)."""
    w = ood_context.get_backend(kind=OODBackendKind.WELFORD, key=key, warmup=warmup)
    raw = ood_context.raw_features(obs)
    score = w.score(raw)
    in_warmup = w.in_warmup  # type: ignore[attr-defined]
    meta: dict[str, Any] = {
        "score": score,
        "threshold": z_threshold,
        "backend": "welford",
        "warmup": in_warmup,
    }
    if not in_warmup and score > z_threshold:
        return CallbackResult.violate(
            bname,
            f"OOD z-score={score:.2f} > threshold={z_threshold:.2f} (Welford)",
            metadata=meta,
        )
    w.observe(raw)  # type: ignore[attr-defined] — only fold in in-distribution samples
    return CallbackResult.ok(bname, metadata=meta)


@boundary_callback(
    name="ood_welford",
    layer="L0",
    description="OOD detection via online Welford z-score (no training data needed).",
    params={
        "z_threshold": "Z-score threshold above which the observation is treated as out-of-distribution.",
        "warmup": "Number of initial observations used to estimate the online normal baseline.",
    },
)
def ood_welford(
    *,
    obs: Observation,
    ood_context: OODContext,
    z_threshold: float = _WELFORD_Z_THRESHOLD,
    warmup: int = 30,
) -> CallbackResult:
    bname = "ood_welford"
    try:
        return _welford_verdict(
            bname=bname,
            ood_context=ood_context,
            obs=obs,
            key=_key(bname, "", "", "welford"),
            z_threshold=z_threshold,
            warmup=warmup,
        )
    except Exception as exc:  # noqa: BLE001 — surface as a structured FAULT
        return CallbackResult.fault(bname, exc, fault_source="ood_backend")


@boundary_callback(
    name="ood_memory_bank",
    layer="L0",
    description="OOD detection via nearest-neighbour distance to a memory bank of normals.",
    params={
        "nn_threshold": "Nearest-neighbour distance threshold above which the observation is OOD.",
        "ood_model_path": "Path to the trained OOD encoder/model artifact.",
        "bank_path": "Path to the memory bank of normal features.",
        "device": "Device used by the OOD backend, such as cpu, cuda, or mps.",
        "warmup": "Number of fallback Welford warm-up observations when the backend is not ready.",
        "vision_model": "Optional HuggingFace vision model for image feature extraction (e.g. mobilenet_v3_large).",
        "vision_weight": "Weight of vision features in the fused embedding (0.0-1.0, default 0.3).",
        "vision_camera": "Camera image key used for pretrained vision features.",
    },
)
def ood_memory_bank(
    *,
    obs: Observation,
    ood_context: OODContext,
    nn_threshold: float = 2.0,
    ood_model_path: str = "",
    bank_path: str = "",
    device: str = "cpu",
    warmup: int = 30,
    vision_model: str = "",
    vision_weight: float = 0.3,
    vision_camera: str = "",
) -> CallbackResult:
    bname = "ood_memory_bank"
    try:
        if vision_model:
            ood_context.configure_vision(
                vision_model, vision_weight, device, vision_camera=vision_camera or None
            )
        key = _key(bname, ood_model_path, bank_path, "memory_bank")
        backend = ood_context.get_backend(kind=OODBackendKind.MEMORY_BANK, key=key, device=device)
        ood_context.load_backend(
            backend,
            key=key,
            obs=obs,
            model_path=ood_model_path or None,
            bank_path=bank_path or None,
            device=device,
        )
        if not backend.is_ready():
            return _welford_verdict(
                bname=bname,
                ood_context=ood_context,
                obs=obs,
                key=f"{key}::welford",
                z_threshold=_WELFORD_Z_THRESHOLD,
                warmup=warmup,
            )
        z = ood_context.features(obs)
        score = backend.score(z)
        meta = {"score": score, "threshold": nn_threshold, "backend": "memory_bank"}
        if score > nn_threshold:
            return CallbackResult.violate(
                bname,
                f"OOD nn_distance={score:.4f} > threshold={nn_threshold:.4f}",
                metadata=meta,
            )
        return CallbackResult.ok(bname, metadata=meta)
    except Exception as exc:  # noqa: BLE001
        return CallbackResult.fault(bname, exc, fault_source="ood_backend")


@boundary_callback(
    name="ood_normalizing_flow",
    layer="L0",
    description="OOD detection via Real-NVP normalizing-flow negative log-likelihood.",
    params={
        "nll_sigma": "Sigma multiplier used when deriving a learned NLL threshold.",
        "nll_threshold": "Fallback negative log-likelihood threshold.",
        "ood_model_path": "Path to the trained normalizing-flow model artifact.",
        "bank_path": "Path to saved OOD calibration data.",
        "device": "Device used by the OOD backend, such as cpu, cuda, or mps.",
        "warmup": "Number of fallback Welford warm-up observations when the backend is not ready.",
        "vision_model": "Optional HuggingFace vision model for image feature extraction (e.g. mobilenet_v3_large).",
        "vision_weight": "Weight of vision features in the fused embedding (0.0-1.0, default 0.3).",
        "vision_camera": "Camera image key used for pretrained vision features.",
    },
)
def ood_normalizing_flow(
    *,
    obs: Observation,
    ood_context: OODContext,
    nll_sigma: float = 3.0,
    nll_threshold: float = 5.0,
    ood_model_path: str = "",
    bank_path: str = "",
    device: str = "cpu",
    warmup: int = 30,
    vision_model: str = "",
    vision_weight: float = 0.3,
    vision_camera: str = "",
) -> CallbackResult:
    bname = "ood_normalizing_flow"
    try:
        if vision_model:
            ood_context.configure_vision(
                vision_model, vision_weight, device, vision_camera=vision_camera or None
            )
        key = _key(bname, ood_model_path, bank_path, "normalizing_flow")
        backend = ood_context.get_backend(
            kind=OODBackendKind.NORMALIZING_FLOW, key=key, device=device
        )
        ood_context.load_backend(
            backend,
            key=key,
            obs=obs,
            model_path=ood_model_path or None,
            bank_path=bank_path or None,
            device=device,
        )
        if not backend.is_ready():
            return _welford_verdict(
                bname=bname,
                ood_context=ood_context,
                obs=obs,
                key=f"{key}::welford",
                z_threshold=_WELFORD_Z_THRESHOLD,
                warmup=warmup,
            )
        z = ood_context.features(obs)
        score = backend.score(z)
        threshold = backend.threshold(nll_sigma=nll_sigma, nll_threshold=nll_threshold)  # type: ignore[attr-defined]
        meta = {"score": score, "threshold": threshold, "backend": "normalizing_flow"}
        if score > threshold:
            return CallbackResult.violate(
                bname,
                f"OOD nll={score:.4f} > threshold={threshold:.4f}",
                metadata=meta,
            )
        return CallbackResult.ok(bname, metadata=meta)
    except Exception as exc:  # noqa: BLE001
        return CallbackResult.fault(bname, exc, fault_source="ood_backend")


__all__ = ["ood_memory_bank", "ood_normalizing_flow", "ood_welford"]
