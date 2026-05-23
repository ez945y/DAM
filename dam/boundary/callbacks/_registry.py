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

_PARAM_DESCRIPTIONS = {
    "bounds": "Axis-aligned allowed box. Workspace uses [[xmin,xmax],[ymin,ymax],[zmin,zmax]] in metres.",
    "upper": "Per-joint upper position limits. Radians by default unless use_degrees is true.",
    "lower": "Per-joint lower position limits. Radians by default unless use_degrees is true.",
    "max_velocities": "Per-joint max velocity. Radians/sec by default unless use_degrees is true.",
    "use_degrees": "Interpret joint limits or velocities as degrees instead of radians.",
    "slack_weight": "QP soft-constraint penalty. Higher values make violating this limit more expensive.",
    "cbf_gamma": "Workspace CBF decay in [0,1]. 1 is a hard one-step bound; lower values brake earlier.",
    "max_cpu_percent": "Host CPU usage percentage above which host_health_limit faults.",
    "max_memory_percent": "Host memory usage percentage above which host_health_limit faults.",
    "max_temperature_c": "Host or device temperature threshold in Celsius, depending on callback.",
    "max_gpu_percent": "GPU usage percentage above which host_health_limit faults.",
    "max_gpu_temperature_c": "GPU temperature threshold in Celsius above which host_health_limit faults.",
    "max_staleness_ms": "Maximum age of the latest observation before hardware_watchdog treats it as stale.",
    "consecutive_fault_frames": "Number of consecutive hardware fault frames before stopping for recoverable peaks.",
    "peak_action": "What to do with hardware peaks: warn, log, or record.",
    "channel": "Observation channel name to read from obs.channels.",
    "max_force_n": "Maximum allowed force magnitude in Newtons.",
    "max_torque_nm": "Maximum allowed torque magnitude in Newton-metres.",
    "max_linear_speed": "Maximum end-effector linear speed in m/s.",
    "max_angular_speed": "Maximum end-effector angular speed in rad/s.",
    "frame": "Optional dynamics frame id/name. Leave empty to use the robot default EE frame.",
    "boxes": "Keep-out boxes as a list of [[xmin,xmax],[ymin,ymax],[zmin,zmax]] regions.",
    "spheres": "Keep-out spheres as a list of [cx,cy,cz,radius] regions in metres.",
    "max_tilt_deg": "Maximum allowed tool tilt from the reference axis, in degrees.",
    "reference_axis": "World-frame axis to align with. Defaults to [0,0,1].",
    "tool_axis": "Tool-frame axis that should stay aligned. Defaults to [0,0,1].",
    "polygon": "Allowed base geofence polygon as [[x,y], ...].",
    "allowed_command": "Allowed gripper command for this task node: close, open, or none.",
    "zone": "EE zone where the allowed gripper command may run: [[xmin,xmax],[ymin,ymax],[zmin,zmax]].",
    "close_threshold": "gripper_action <= this value is treated as close.",
    "open_threshold": "gripper_action >= this value is treated as open.",
}


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
            if p_name in _RUNTIME_ONLY_PARAMS:
                continue
            params_meta[p_name] = {
                "default": param.default if param.default is not inspect.Parameter.empty else None,
                "has_default": param.default is not inspect.Parameter.empty,
                "description": _PARAM_DESCRIPTIONS.get(p_name, ""),
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
