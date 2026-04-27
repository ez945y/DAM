from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from dam.guard.layer import GuardLayer
from dam.registry.callback import get_global_registry

G = TypeVar("G", bound=type)


def guard(
    layer: str,
    _process_group: str | None = None,
) -> Callable[[G], G]:
    """Class decorator for Guard subclasses. Validates and caches signature at import time."""
    try:
        layer_enum = GuardLayer[layer]
    except KeyError:
        valid = [layer.name for layer in GuardLayer]
        msg = f"Unknown guard layer '{layer}'. Valid layers: {valid}"
        raise ValueError(msg) from None

    def decorator(cls: G) -> G:
        # Cache parameter names from check() at decoration time
        sig = inspect.signature(cls.check)
        param_names = [p for p in sig.parameters if p != "self" and p != "kwargs"]
        cls._guard_layer = layer_enum
        cls._cached_param_names = param_names
        # Initialize injection slots (will be filled at startup by precompute_injection)
        cls._static_kwargs: dict[str, Any] = {}
        cls._runtime_keys: list[str] = []
        return cls

    return decorator


def callback(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Validate runtime-pool keys and register in global CallbackRegistry at import time."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        get_global_registry().register(name=name, fn=fn, valid_keys=None)
        return fn

    return decorator


def fallback(
    name: str,
    escalates_to: str | None = None,
) -> Callable[[G], G]:
    """Class decorator for Fallback subclasses."""

    def decorator(cls: G) -> G:
        cls._fallback_name = name
        cls._escalates_to = escalates_to
        from dam.fallback.registry import get_global_registry

        with contextlib.suppress(ValueError):
            get_global_registry().register(cls())
        return cls

    return decorator
