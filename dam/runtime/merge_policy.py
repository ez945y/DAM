"""Data-defined parameter-merge policy for boundary-node config pools.

When two boundaries contribute the same parameter name (e.g. both clamp
``upper`` joint limits), :func:`GuardRuntime._build_config_pool` must decide
how to combine the values.  We could hard-code the strategies inside the
runtime, but that means every new safety primitive (QP slack weights, CBF
alpha, …) requires editing the runtime.

Instead, strategies live in a registry keyed by parameter name.  Built-in
safety rules register themselves at module import time; new guards do the
same.  ``_build_config_pool`` just calls :func:`resolve` and applies the
returned function — no parameter-name special cases in the runtime.

Default for unregistered names: last-write-wins (``overwrite``), with a
warning logged by the runtime when the new value disagrees with the old.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MergeFn = Callable[[Any, Any], Any]


# ── Built-in merge strategies ─────────────────────────────────────────────


def overwrite(_old: Any, new: Any) -> Any:
    """Last writer wins.  Default for unregistered parameters."""
    return new


def take_min(old: Any, new: Any) -> Any:
    """Scalar minimum — typical for "max allowed X" hard limits."""
    return min(old, new)


def take_max(old: Any, new: Any) -> Any:
    """Scalar maximum — typical for "minimum reaction time" or slack weights
    (more boundaries demanding strict enforcement → stricter wins)."""
    return max(old, new)


def elementwise_min(old: Any, new: Any) -> list[float]:
    """Per-element minimum on array-like values."""
    result: list[float] = np.minimum(
        np.asarray(old, dtype=float), np.asarray(new, dtype=float)
    ).tolist()
    return result


def elementwise_max(old: Any, new: Any) -> list[float]:
    """Per-element maximum on array-like values."""
    result: list[float] = np.maximum(
        np.asarray(old, dtype=float), np.asarray(new, dtype=float)
    ).tolist()
    return result


# ── Registry ──────────────────────────────────────────────────────────────

_REGISTRY: dict[str, MergeFn] = {}


def register(name: str, fn: MergeFn) -> None:
    """Register a merge strategy for parameter *name*.

    Idempotent — re-registering the same name silently overwrites.  Strategy
    callables receive ``(old_value, new_value)`` and return the combined
    value.  They MUST be pure (no I/O, no mutation) so the runtime can call
    them inside ``_build_config_pool`` during validate-before-commit.
    """
    _REGISTRY[name] = fn


def resolve(name: str) -> MergeFn:
    """Return the merge strategy for *name*, or :func:`overwrite` as default."""
    return _REGISTRY.get(name, overwrite)


def registered_names() -> list[str]:
    """Return a sorted snapshot of registered parameter names (for diagnostics)."""
    return sorted(_REGISTRY)


# ── Built-in safety-critical entries ──────────────────────────────────────
# Anything that constrains motion is registered here.  Guards that introduce
# new safety-critical params should register from their own module's import
# time so the registry is populated before the runtime ever calls resolve().

# Scalar caps — tightest wins
register("max_speed", take_min)
register("max_jerk", take_min)
register("max_staleness_ms", take_min)

# Per-joint vector caps
register("upper", elementwise_min)
register("max_velocity", elementwise_min)
register("max_acceleration", elementwise_min)

# Per-joint vector floors
register("lower", elementwise_max)

# Slack weights — more boundaries demanding strict → stricter (larger) wins
register("slack_weight", take_max)

# CBF alpha — multiple boundaries → take the SMALLER (more conservative
# / earlier braking).  Larger α defers braking, smaller α brakes earlier.
register("cbf_alpha", take_min)

# Observation-channel constraint thresholds — tightest (safest) wins.
register("max_temperature_c", take_min)
register("max_current_a", take_min)
register("max_voltage_v", take_min)
register("min_voltage_v", take_max)  # higher floor → more conservative
register("max_force_n", take_min)
