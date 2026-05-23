"""Boundary-callback registry infrastructure (layer-agnostic).

Holds the ``@boundary_callback`` decorator, the metadata catalog, and
``register_all()``.  Per-layer callback modules import the decorator from
here; the package ``__init__`` imports every layer module so the decorators
fire at import time and populate ``_CALLBACKS`` / ``_CATALOG``.

Registration stays deferred to ``register_all()`` (called by the runtime /
service init) so importing the package is side-effect free, per the
project's "no import-time side effects" rule.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Mapping
from typing import Any

from dam.registry.callback import get_global_registry

logger = logging.getLogger(__name__)


# ── Metadata store ────────────────────────────────────────────────────────────

_CATALOG: list[dict[str, Any]] = []  # [{name, layer, description, params, doc}, ...]

# name → fn, populated by @boundary_callback at import time.  register_all()
# iterates this so new callbacks are picked up automatically — no manual list
# to keep in sync.
_CALLBACKS: dict[str, Callable[..., Any]] = {}

_RUNTIME_ONLY_PARAMS = {
    "obs",
    "action",
    "dt",
    "now",
    "host_health",
    "kinematics_resolver",
    "dynamics",
    "camera_shapes",
    # Legacy aliases / compatibility params that should not be authored in new stackfiles.
    "cbf_alpha",
}


def boundary_callback(
    *,
    name: str,
    layer: str,
    description: str = "",
    params: Mapping[str, str] | None = None,
    unit_params: tuple[str, ...] | list[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a function as a named boundary callback.

    ``unit_params`` lists params whose values are interpreted in degrees when
    ``use_degrees=True`` is set on the boundary; the loader normalises them to
    radians once at config-pool build time so the callback hot path never
    touches a unit branch.  ``use_degrees`` itself stays in the catalog as a
    UI hint and is stripped from the pool after normalisation.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        sig = inspect.signature(fn)
        param_descriptions = params or {}
        params_meta = {}
        for p_name, param in sig.parameters.items():
            if p_name in _RUNTIME_ONLY_PARAMS:
                continue
            params_meta[p_name] = {
                "default": param.default if param.default is not inspect.Parameter.empty else None,
                "has_default": param.default is not inspect.Parameter.empty,
                "description": param_descriptions.get(p_name, ""),
            }
        doc = fn.__doc__ or ""
        unit_params_tuple: tuple[str, ...] = tuple(unit_params or ())

        # Surface ``use_degrees`` in the catalog as a UI hint whenever the
        # callback declared unit-sensitive params; the runtime hot path never
        # receives it (loader strips after normalisation), so it is not in
        # the function signature.
        if unit_params_tuple and "use_degrees" not in params_meta:
            params_meta["use_degrees"] = {
                "default": False,
                "has_default": True,
                "description": param_descriptions.get(
                    "use_degrees",
                    f"UI/loader hint: interpret {', '.join(unit_params_tuple)} as deg/s. "
                    "Normalised to rad/s once at load.",
                ),
            }

        fn._cb_name = name  # type: ignore[attr-defined]
        fn._cb_layer = layer  # type: ignore[attr-defined]
        fn._cb_description = description or (doc.split("\n")[0] if doc else "")  # type: ignore[attr-defined]
        fn._cb_unit_params = unit_params_tuple  # type: ignore[attr-defined]

        _CATALOG.append(
            {
                "name": name,
                "layer": layer,
                "description": fn._cb_description,
                "params": params_meta,
                "unit_params": list(unit_params_tuple),
                "doc": doc,
            }
        )
        if name in _CALLBACKS and _CALLBACKS[name] is not fn:
            raise ValueError(f"Duplicate boundary callback name: {name!r}")
        _CALLBACKS[name] = fn
        return fn

    return decorator


_DEG2RAD = 3.141592653589793 / 180.0


def normalize_unit_params(callback_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``params`` with unit-aware fields converted to radians once.

    If the callback declared ``unit_params=[...]`` AND the node sets
    ``use_degrees=True``, each listed param is scalar/array-multiplied by
    π/180 and ``use_degrees`` is stripped from the result.  Otherwise the
    params are returned untouched (with ``use_degrees`` stripped if present
    — runtime callbacks never see it).
    """
    out: dict[str, Any] = dict(params)
    use_deg = bool(out.pop("use_degrees", False))
    fn = _CALLBACKS.get(callback_name)
    unit_keys: tuple[str, ...] = getattr(fn, "_cb_unit_params", ()) if fn is not None else ()
    if not use_deg or not unit_keys:
        return out
    import numpy as np

    for key in unit_keys:
        if key not in out or out[key] is None:
            continue
        val = out[key]
        if isinstance(val, (int, float)):
            out[key] = float(val) * _DEG2RAD
        else:
            arr = np.asarray(val, dtype=np.float64)
            out[key] = (arr * _DEG2RAD).tolist()
    return out


def get_catalog() -> list[dict[str, Any]]:
    """Return a copy of the full callback catalog (name, layer, description, params, doc)."""
    return list(_CATALOG)


def register_all() -> None:
    """Register every ``@boundary_callback``-decorated function.

    Auto-discovered from ``_CALLBACKS`` (populated by the decorators when the
    package is imported), so adding a new callback module needs no edit here.
    """
    reg = get_global_registry()
    for name, fn in _CALLBACKS.items():
        with contextlib.suppress(ValueError):
            reg.register(name, fn)

    logger.info("DAM: %d built-in boundary callbacks registered [L0-L3]", len(_CALLBACKS))
