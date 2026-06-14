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


def callback(name: str, *, layer: str = "L1") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a boundary callback in the global CallbackRegistry at import time.

    ``layer`` (``L0``–``L3``) tags which guard dispatches it; it must match the
    ``layer`` of the boundary that references it. The callback declares the
    observation groups and runtime values it needs as keyword parameters
    (``obs``, ``action``, ``base_pose``, ``current``, ``dt``, …) — the runtime
    injects them by name.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._cb_layer = layer  # type: ignore[attr-defined]
        get_global_registry().register(name=name, fn=fn, valid_keys=None)
        return fn

    return decorator


def solver_factory(
    name: str,
    *,
    capabilities: tuple[str, ...] | list[str],
    replace: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a config-driven solver factory under ``name``.

    Mirrors @dam.guard / @dam.callback / @dam.fallback. The factory declares
    the parameters it needs as **keyword arguments**; DAM injects matching
    values from the stackfile ``params`` plus robot context the runtime knows
    (``asset_path``, ``asset_type``, ``asset``, ``observation_joint_names``).
    Parameters the factory does not declare are dropped — no ``params.get()``
    boilerplate, no swallowing of keys you never asked for. Declare ``**kwargs``
    to receive everything.

    The name you register under IS the name stackfiles reference (the
    solvers-block key); there is no separate ``type``.

    .. code-block:: python

        @dam.solver_factory("ackermann", capabilities=["rollout"])
        def make_ackermann(wheel_base=None, track_width=None):
            return AckermannSolver(wheel_base, track_width)

    .. code-block:: yaml

        solvers:
          ackermann:            # key == registered name
            params: { wheel_base: 0.32 }
    """
    from dam.solver.registry import get_global_solver_registry

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        get_global_solver_registry().register_factory(
            name, fn, capabilities=capabilities, replace=replace
        )
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
