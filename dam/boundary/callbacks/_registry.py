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
from collections.abc import Callable
from typing import Any

from dam.registry.callback import get_global_registry

logger = logging.getLogger(__name__)


# ── Metadata store ────────────────────────────────────────────────────────────

_CATALOG: list[dict[str, Any]] = []  # [{name, layer, description, params, doc}, ...]

# name → fn, populated by @boundary_callback at import time.  register_all()
# iterates this so new callbacks are picked up automatically — no manual list
# to keep in sync.
_CALLBACKS: dict[str, Callable[..., Any]] = {}


def boundary_callback(
    *,
    name: str,
    layer: str,
    description: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a function as a named boundary callback."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        sig = inspect.signature(fn)
        params_meta = {}
        for p_name, param in sig.parameters.items():
            if p_name == "obs":
                continue
            params_meta[p_name] = {
                "default": param.default if param.default is not inspect.Parameter.empty else None,
                "has_default": param.default is not inspect.Parameter.empty,
            }
        doc = fn.__doc__ or ""

        fn._cb_name = name  # type: ignore[attr-defined]
        fn._cb_layer = layer  # type: ignore[attr-defined]
        fn._cb_description = description or (doc.split("\n")[0] if doc else "")  # type: ignore[attr-defined]

        _CATALOG.append(
            {
                "name": name,
                "layer": layer,
                "description": fn._cb_description,
                "params": params_meta,
                "doc": doc,
            }
        )
        if name in _CALLBACKS and _CALLBACKS[name] is not fn:
            raise ValueError(f"Duplicate boundary callback name: {name!r}")
        _CALLBACKS[name] = fn
        return fn

    return decorator


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
