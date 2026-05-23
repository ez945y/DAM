"""L0 — Perception boundary callbacks (out-of-distribution detection)."""

from __future__ import annotations

import threading
from typing import Any

from dam.boundary.callbacks._registry import boundary_callback
from dam.types.observation import Observation

_ood_guard_cache: dict[tuple[str, str, str], Any] = {}
_ood_cache_lock = threading.Lock()


@boundary_callback(
    name="ood_detector",
    layer="L0",
    description="Out-of-distribution boundary callback — wraps OODGuard.",
    params={
        "ood_model_path": "Path to the trained OOD model artifact.",
        "bank_path": "Path to the memory bank or calibration artifact.",
        "nn_threshold": "Nearest-neighbour distance threshold for memory-bank OOD.",
        "nll_threshold": "Negative log-likelihood threshold for flow-based OOD.",
        "backend": "OOD backend to run, e.g. memory_bank.",
        "temporal_smoothing_frames": "Consecutive OOD frames required before rejecting.",
    },
)
def ood_detector(
    *,
    obs: Observation,
    ood_model_path: str = "",
    bank_path: str = "",
    nn_threshold: float = 2.0,
    nll_threshold: float = 5.0,
    backend: str = "memory_bank",
    temporal_smoothing_frames: int = 3,
) -> bool:
    """Return False if the observation is flagged as out-of-distribution."""
    from dam.decorators import guard as _guard_deco
    from dam.guard.builtin.ood import OODGuard

    decorated_ood = _guard_deco("L0")(OODGuard)
    smoothing_frames = max(1, int(temporal_smoothing_frames))
    cache_key = (ood_model_path, bank_path, backend)

    with _ood_cache_lock:
        if cache_key not in _ood_guard_cache:
            guard = decorated_ood(backend=backend)
            if ood_model_path and bank_path:
                try:
                    joint_dim = len(obs.joint_positions)
                    has_images = obs.images is not None and len(obs.images) > 0
                    guard.load(ood_model_path, bank_path, joint_dim, has_images)
                except Exception:  # noqa: BLE001 — guard runs untrained if model files are missing/invalid
                    pass
            _ood_guard_cache[cache_key] = guard
        guard = _ood_guard_cache[cache_key]

    from dam.types.result import GuardDecision

    result = guard.check(
        obs,
        nn_threshold=nn_threshold,
        nll_threshold=nll_threshold,
        ood_model_path=ood_model_path or None,
        bank_path=bank_path or None,
        temporal_smoothing_frames=smoothing_frames,
    )
    return result.decision == GuardDecision.PASS
