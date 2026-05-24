from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar, cast

from dam.guard.layer import (
    LAYER_DEFAULT_ALWAYS,
    LAYER_DEFAULT_PHASE,
    GuardLayer,
)
from dam.registry.callback import get_global_registry

G = TypeVar("G", bound=type)


def guard(
    layer: str,
    _process_group: str | None = None,
    *,
    phase: int | None = None,
    always: bool | None = None,
) -> Callable[[G], G]:
    """Class decorator for Guard subclasses. Validates and caches signature at import time.

    Pipeline placement (phased chassis):
      - ``phase``: which sequential phase the guard runs in (same phase = parallel).
        Defaults: L0,L1 → 0; L2 → 1.
      - ``always``: if True, guard runs in parallel to *all* phases (not bound to
        any phase); its result joins the aggregate at cycle end. Default: L3 → True.
      - ``always=True`` and ``phase`` are mutually exclusive at runtime — always-on
        guards have no phase position; an explicit ``phase`` is silently ignored.
    """
    try:
        layer_enum = GuardLayer[layer]
    except KeyError:
        valid = [layer.name for layer in GuardLayer]
        msg = f"Unknown guard layer '{layer}'. Valid layers: {valid}"
        raise ValueError(msg) from None

    resolved_always = always if always is not None else LAYER_DEFAULT_ALWAYS.get(layer_enum, False)
    if resolved_always:
        resolved_phase: int | None = None
    else:
        resolved_phase = phase if phase is not None else LAYER_DEFAULT_PHASE.get(layer_enum, 0)

    def decorator(cls: G) -> G:
        cls_any = cast(Any, cls)
        # Cache parameter names from check() at decoration time
        sig = inspect.signature(cls_any.check)
        param_names = [p for p in sig.parameters if p != "self" and p != "kwargs"]
        cls_any._guard_layer = layer_enum
        cls_any._guard_phase = resolved_phase  # int or None (when always=True)
        cls_any._guard_always = resolved_always  # bool
        cls_any._cached_param_names = param_names
        # Initialize injection slots (will be filled at startup by precompute_injection)
        cls_any._static_kwargs = {}
        cls_any._runtime_keys = []
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
    *,
    monitors_hardware: bool | None = None,
) -> Callable[[G], G]:
    """Register a StepContext subclass as a stackfile fallback Context."""

    def decorator(cls: G) -> G:
        from dam.runtime.context import StepContext, register_context

        if not issubclass(cls, StepContext):
            msg = "@dam.fallback can only decorate StepContext subclasses"
            raise TypeError(msg)
        ctx_cls = cast(type[StepContext], cls)
        ctx_cls.name = name
        if monitors_hardware is not None:
            ctx_cls.monitors_hardware = monitors_hardware
        register_context(name, ctx_cls)
        return cls

    return decorator
