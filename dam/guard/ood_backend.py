"""OOD detector backends — a uniform interface over the three OOD algorithms.

Phase 6 of the guard-pipeline refactor pulls the per-algorithm dispatch out of
``OODGuard.check()`` (which hard-coded ``"welford" / "memory_bank" /
"normalizing_flow"`` branches) and behind a small :class:`OODBackend` Protocol.
Each backend wraps one of the existing detectors
(:class:`~dam.guard.builtin.ood.MemoryBank`,
:class:`~dam.guard.builtin.ood.RealNVPFlow`,
:class:`~dam.guard.builtin.ood._WelfordStats`) and exposes the same shape:

    score(features) -> float     # higher == more out-of-distribution
    train(features)  -> None
    is_ready()       -> bool
    diagnostics()    -> dict
    save(path) / load(path)

The detector classes themselves are untouched (tests import them directly), so
the backends only delegate.  Detector imports are deferred to call time to keep
this module free of an import cycle once ``OODGuard`` starts depending on it.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import numpy as np

if TYPE_CHECKING:
    from dam.guard.builtin.ood import MemoryBank, RealNVPFlow, _WelfordStats

_WARMUP_SAMPLES = 30


class OODBackendKind(Enum):
    """The three built-in OOD algorithms, weakest → strongest."""

    WELFORD = "welford"
    MEMORY_BANK = "memory_bank"
    NORMALIZING_FLOW = "normalizing_flow"

    @classmethod
    def from_value(cls, value: OODBackendKind | str) -> OODBackendKind:
        """Coerce a string (or enum) to a kind, accepting a few legacy aliases.

        Raises ``ValueError`` on an unknown backend so a typo in a stackfile
        fails loudly rather than silently picking a default.
        """
        if isinstance(value, cls):
            return value
        s = str(value).strip().lower()
        try:
            return cls(s)
        except ValueError:
            aliases = {
                "flow": cls.NORMALIZING_FLOW,
                "nvp": cls.NORMALIZING_FLOW,
                "realnvp": cls.NORMALIZING_FLOW,
                "real_nvp": cls.NORMALIZING_FLOW,
                "memorybank": cls.MEMORY_BANK,
                "memory-bank": cls.MEMORY_BANK,
                "knn": cls.MEMORY_BANK,
            }
            if s in aliases:
                return aliases[s]
            valid = [k.value for k in cls]
            raise ValueError(f"Unknown OOD backend {value!r}. Valid: {valid}") from None


@runtime_checkable
class OODBackend(Protocol):
    """Uniform interface every OOD algorithm presents to the L0 callbacks.

    ``score`` returns a scalar where **higher means more out-of-distribution**;
    the callback compares it against ``threshold(sigma)``.

    ``is_ready`` returns True when the backend can produce actionable scores.
    Welford returns True even during warmup because it deliberately produces
    conservative (in-distribution) scores until enough samples accumulate.
    """

    kind: OODBackendKind

    def is_ready(self) -> bool: ...

    def score(self, features: np.ndarray) -> float: ...

    def train(self, features: np.ndarray) -> None: ...

    def threshold(self, sigma: float, calibrated_threshold: float | None = None) -> float:
        """Effective OOD cutoff for the given sensitivity.

        ``calibrated_threshold`` is used by normalizing-flow when an EER/RQ1
        value is available; other backends ignore it.
        """
        ...

    def observe(self, features: np.ndarray) -> None:
        """Feed an in-distribution sample for online adaptation (Welford only).

        Default is a no-op for backends that don't adapt online.
        """
        ...

    @property
    def in_warmup(self) -> bool:
        """True while the backend is accumulating initial statistics."""
        ...

    def diagnostics(self) -> dict[str, Any]: ...

    def save(self, path: str | Path) -> None: ...

    def load(self, path: str | Path) -> None: ...


# ── Welford ────────────────────────────────────────────────────────────────────


class WelfordBackend:
    """Online z-score detector (no training data, no deps).

    Special among the backends: it adapts online.  During ``warmup`` it only
    accumulates statistics and reports in-distribution; afterwards ``score``
    returns the max per-feature z-score.  The caller decides whether to
    ``observe`` a sample (the historical guard only folds in samples it deemed
    in-distribution, so OOD frames don't poison the running statistics).
    """

    kind = OODBackendKind.WELFORD

    def __init__(self, warmup: int = _WARMUP_SAMPLES) -> None:
        from dam.guard.builtin.ood import _WelfordStats

        self._w: _WelfordStats = _WelfordStats()
        self._warmup = max(1, int(warmup))

    @property
    def in_warmup(self) -> bool:
        return self._w.n < self._warmup

    def is_ready(self) -> bool:
        return True  # always usable; warm-up just passes

    def score(self, features: np.ndarray) -> float:
        if self.in_warmup:
            return 0.0
        return self._w.z_score_max(np.asarray(features, dtype=np.float64))

    def threshold(self, sigma: float, calibrated_threshold: float | None = None) -> float:
        return sigma

    def observe(self, features: np.ndarray) -> None:
        self._w.update(np.asarray(features, dtype=np.float64))

    def train(self, features: np.ndarray) -> None:
        # Online detector: fold a batch of normal samples in one by one.
        arr = np.asarray(features, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[np.newaxis, :]
        for row in arr:
            self._w.update(row)

    def diagnostics(self) -> dict[str, Any]:
        return {"welford_samples": self._w.n}

    def save(self, path: str | Path) -> None:
        np.savez(
            str(path),
            n=self._w.n,
            mean=self._w.mean if self._w.mean is not None else np.array([]),
            m2=self._w.m2 if self._w.m2 is not None else np.array([]),
        )

    def load(self, path: str | Path) -> None:
        data = np.load(str(path))
        self._w.n = int(data["n"])
        self._w.mean = data["mean"] if data["mean"].size else None
        self._w.m2 = data["m2"] if data["m2"].size else None


# ── Memory Bank ──────────────────────────────────────────────────────────────


class MemoryBankBackend:
    """Nearest-neighbour distance against a bank of normal embeddings."""

    kind = OODBackendKind.MEMORY_BANK

    def __init__(self, bank: MemoryBank | None = None) -> None:
        from dam.guard.builtin.ood import MemoryBank

        self._bank: MemoryBank = bank or MemoryBank()
        self.mean_train_dist: float | None = None
        self.std_train_dist: float | None = None

    def is_ready(self) -> bool:
        return self._bank.is_trained

    def score(self, features: np.ndarray) -> float:
        return self._bank.nearest_distance(np.asarray(features, dtype=np.float32))

    def train(self, features: np.ndarray) -> None:
        arr = np.asarray(features, dtype=np.float32)
        self._bank.train(arr)
        if self._bank.is_trained and arr.ndim == 2 and arr.shape[0] > 1:
            dists = np.array([self._bank.nearest_distance(row) for row in arr], dtype=np.float64)
            self.mean_train_dist = float(np.mean(dists))
            self.std_train_dist = float(np.std(dists))

    def threshold(self, sigma: float, calibrated_threshold: float | None = None) -> float:
        if self.mean_train_dist is not None and self.std_train_dist is not None:
            return self.mean_train_dist + sigma * self.std_train_dist
        return sigma

    def observe(self, features: np.ndarray) -> None:
        pass

    @property
    def in_warmup(self) -> bool:
        return False

    def diagnostics(self) -> dict[str, Any]:
        return {
            "bank_trained": self._bank.is_trained,
            "bank_size": self._bank.size,
            "bank_dim": (
                int(self._bank._vectors.shape[1]) if self._bank._vectors is not None else None
            ),
            "bank_backend": self._bank._backend,  # noqa: SLF001 — diagnostics surface
            "mean_train_dist": self.mean_train_dist,
            "std_train_dist": self.std_train_dist,
        }

    def save(self, path: str | Path) -> None:
        self._bank.save(str(path))

    def load(self, path: str | Path) -> None:
        self._bank.load(str(path))


# ── Normalizing Flow ─────────────────────────────────────────────────────────


class RealNVPFlowBackend:
    """Real-NVP normalizing-flow density model; score = -log p(z).

    Records the training NLL distribution (``mean_train_nll`` /
    ``std_train_nll``) so a caller can derive a σ-based threshold the same way
    the old guard did.
    """

    kind = OODBackendKind.NORMALIZING_FLOW

    def __init__(self, flow: RealNVPFlow | None = None, device: str = "cpu") -> None:
        self._flow: RealNVPFlow | None = flow
        self._device = device
        self.mean_train_nll: float | None = None
        self.std_train_nll: float | None = None

    def is_ready(self) -> bool:
        return self._flow is not None and self._flow.is_fitted

    def score(self, features: np.ndarray) -> float:
        if self._flow is None:
            return 0.0
        return self._flow.neg_log_prob(np.asarray(features, dtype=np.float32))

    def train(
        self,
        features: np.ndarray,
        epochs: int | None = None,
        lr: float | None = None,
        verbose: bool = False,
    ) -> None:
        from dam.guard.builtin.ood import _FLOW_EPOCHS, _FLOW_LR, RealNVPFlow

        vectors = np.asarray(features, dtype=np.float32)
        if vectors.ndim != 2:
            raise ValueError(f"flow.train expects (N, dim), got shape {vectors.shape}")
        if self._flow is None:
            self._flow = RealNVPFlow(dim=vectors.shape[1], device=self._device)
        self._flow.fit(
            vectors,
            epochs=_FLOW_EPOCHS if epochs is None else epochs,
            lr=_FLOW_LR if lr is None else lr,
            verbose=verbose,
        )
        train_nlls = self._flow.neg_log_prob_batch(vectors)
        self.mean_train_nll = float(np.mean(train_nlls))
        self.std_train_nll = float(np.std(train_nlls))

    def threshold(self, sigma: float, calibrated_threshold: float | None = None) -> float:
        """Effective cutoff.

        When *calibrated_threshold* is provided (from RQ1 / EER), uses it
        directly.  Otherwise uses mean + σ·std from training statistics.
        Falls back to *sigma* as a raw value when no stats are available.
        """
        if calibrated_threshold is not None:
            return calibrated_threshold
        if self.mean_train_nll is not None and self.std_train_nll is not None:
            return self.mean_train_nll + sigma * self.std_train_nll
        return sigma

    def observe(self, features: np.ndarray) -> None:
        pass

    @property
    def in_warmup(self) -> bool:
        return False

    def diagnostics(self) -> dict[str, Any]:
        return {
            "flow_fitted": self.is_ready(),
            "mean_train_nll": self.mean_train_nll,
            "std_train_nll": self.std_train_nll,
        }

    def save(self, path: str | Path) -> None:
        if self._flow is not None:
            self._flow.save(
                str(path),
                mean_train_nll=self.mean_train_nll,
                std_train_nll=self.std_train_nll,
            )

    def load(self, path: str | Path) -> None:
        from dam.guard.builtin.ood import RealNVPFlow

        if self._flow is None:
            self._flow = RealNVPFlow(device=self._device)
        mean_nll, std_nll = self._flow.load(str(path), device=self._device)
        if mean_nll is not None:
            self.mean_train_nll = mean_nll
            self.std_train_nll = std_nll


def make_backend(kind: OODBackendKind | str, **kwargs: Any) -> OODBackend:
    """Construct the backend adapter for a kind (string aliases accepted)."""
    k = OODBackendKind.from_value(kind)
    if k is OODBackendKind.WELFORD:
        warmup = kwargs.get("warmup", _WARMUP_SAMPLES)
        return WelfordBackend(warmup=warmup)
    if k is OODBackendKind.MEMORY_BANK:
        return MemoryBankBackend()
    return RealNVPFlowBackend(device=kwargs.get("device", "cpu"))


__all__ = [
    "MemoryBankBackend",
    "OODBackend",
    "OODBackendKind",
    "RealNVPFlowBackend",
    "WelfordBackend",
    "make_backend",
]
