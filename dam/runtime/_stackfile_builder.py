"""Stackfile → GuardRuntime construction helpers.

Extracted from ``guard_runtime.py`` to keep the runtime orchestrator focused
on cycle execution.  All functions here are pure construction logic — they
build a GuardRuntime instance and return it, with zero runtime coupling.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from dam.injection.static import precompute_injection
from dam.runtime.execution_engine import _filter_kwargs

if TYPE_CHECKING:
    from dam.config.schema import StackfileConfig
    from dam.kinematics.resolver import KinematicsResolver

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ResolvedFallback:
    """Parsed stackfile fallback entry and optional auto-escalation."""

    context_type: str
    params: dict[str, Any] = dataclasses.field(default_factory=dict)
    escalate_after_seconds: float | None = None
    escalate_to: str | None = None


def from_stackfile(runtime_cls: type, path: str) -> Any:
    """Construct a GuardRuntime from a Stackfile YAML path."""
    from dam.config.loader import StackfileLoader

    return from_config(runtime_cls, StackfileLoader.load(path))


def from_config(runtime_cls: type, config: StackfileConfig, frame_hub: Any | None = None) -> Any:
    """Construct a GuardRuntime from an already-parsed StackfileConfig."""
    from dam.registry.callback import get_global_registry as _get_cb_registry
    from dam.registry.guard import get_guard_registry

    kinematics_resolver = _init_kinematics_resolver(config)
    guards_by_kind, boundary_to_kind, boundary_containers = _build_all_boundaries(
        config, _get_cb_registry(), get_guard_registry()
    )

    task_config = {tname: tcfg.boundaries for tname, tcfg in config.tasks.items()}
    always_active = config.safety.always_active_list()

    logger.info(
        "GuardRuntime.from_stackfile: %d guard kind(s) instantiated for %d boundary(s): %s",
        len(guards_by_kind),
        len(boundary_containers),
        list(guards_by_kind.keys()),
    )

    initial_pool = runtime_cls._build_config_pool(config)  # type: ignore[attr-defined]

    runtime = runtime_cls(
        guards=list(guards_by_kind.values()),
        boundary_containers=boundary_containers,
        task_config=task_config,
        always_active=always_active,
        config_pool=initial_pool,
        control_frequency_hz=config.safety.control_frequency_hz,
        enforcement_mode=config.safety.enforcement_mode,
        risk_controller_config=config.risk_controller,
        loopback_config=config.loopback,
        kinematics_resolver=kinematics_resolver,
        boundary_to_kind=boundary_to_kind,
        frame_hub=frame_hub,
        default_fallback=config.safety.no_task_behavior,
    )

    _apply_guard_overrides(config, guards_by_kind, runtime)
    runtime._fallbacks_config = dict(config.fallbacks)

    return runtime


def _apply_guard_overrides(
    config: StackfileConfig, guards_by_kind: dict[str, Any], runtime: Any
) -> None:
    """Per-guard phase/always/timeout override from stackfile guards section."""
    _LAYER_KEYS = {"L0", "L1", "L2", "L3"}
    layer_timeout_overrides: dict[int, float] = {}
    for item in config.guards if isinstance(config.guards, list) else []:
        if not isinstance(item, dict):
            continue
        kind_value: str | None = None
        layer_key: str | None = None
        for k, v in item.items():
            if k in _LAYER_KEYS and isinstance(v, str):
                kind_value = v
                layer_key = k
                break
        if kind_value is None or layer_key is None:
            continue

        timeout_override = item.get("timeout_ms")
        if timeout_override is not None:
            layer_val = int(layer_key[1])
            layer_timeout_overrides[layer_val] = float(timeout_override)

        inst = guards_by_kind.get(kind_value)
        if inst is None:
            continue
        phase_override = item.get("phase")
        always_override = item.get("always")
        if phase_override is None and always_override is None:
            continue
        resolved_always = (
            bool(always_override) if always_override is not None else inst.is_always_on()
        )
        resolved_phase = (
            int(phase_override) if phase_override is not None and not resolved_always else None
        )
        if not resolved_always and resolved_phase is None:
            resolved_phase = inst.get_phase()
        inst.set_phase(resolved_phase, always=resolved_always)
        logger.info(
            "Stackfile override: guard '%s' phase=%s always=%s",
            kind_value,
            resolved_phase,
            resolved_always,
        )
    runtime._layer_timeout_overrides = layer_timeout_overrides


def _init_kinematics_resolver(config: StackfileConfig) -> KinematicsResolver | None:
    if not (config.hardware and config.hardware.urdf_path):
        return None
    from dam.kinematics.resolver import KinematicsResolver

    try:
        return KinematicsResolver(config.hardware.urdf_path)
    except Exception as e:
        logger.warning(
            "GuardRuntime: failed to init KinematicsResolver from %s: %s",
            config.hardware.urdf_path,
            e,
        )
        return None


def _build_all_boundaries(
    config: StackfileConfig,
    _cb_reg: Any,
    _guard_reg: Any,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    _LAYER_KIND_MAP = {
        "L0": "ood",
        "L1": "motion",
        "L2": "execution",
        "L3": "hardware",
    }
    guards_by_kind: dict[str, Any] = {}
    boundary_to_kind: dict[str, str] = {}
    boundary_containers: dict[str, Any] = {}

    for bname, bcfg in config.boundaries.items():
        layer_str = getattr(bcfg, "layer", "L2")
        nodes = []
        for ncfg in bcfg.nodes:
            cb_layer = _resolve_cb_layer(ncfg, layer_str, _cb_reg)
            guard_kind = _LAYER_KIND_MAP.get(cb_layer, "execution")
            _register_guard_if_new(guard_kind, ncfg, cb_layer, guards_by_kind, _guard_reg)
            if guard_kind in guards_by_kind:
                boundary_to_kind[bname] = guard_kind
            nodes.append(_build_boundary_node(ncfg, guard_kind, config))
        boundary_containers[bname] = _make_container(bcfg, nodes)

    _configure_stackfile_guard_instances(config, guards_by_kind)
    return guards_by_kind, boundary_to_kind, boundary_containers


def _configure_stackfile_guard_instances(
    config: StackfileConfig,
    guards_by_kind: dict[str, Any],
) -> None:
    """Apply stackfile-selected guard strategies after all boundaries are known."""
    motion_guard = guards_by_kind.get("motion")
    if motion_guard is None:
        return

    qp_values: list[str] = []
    for bcfg in config.boundaries.values():
        if getattr(bcfg, "layer", None) != "L1":
            continue
        for ncfg in bcfg.nodes:
            qp_solver = ncfg.params.get("qp_solver")
            if qp_solver is not None:
                qp_values.append(str(qp_solver).lower())

    if not qp_values:
        return
    unsupported = sorted({v for v in qp_values if v != "proxsuite"})
    if unsupported:
        raise ValueError(f"Unsupported L1 qp_solver value(s): {unsupported}")

    from dam.runtime import qp_solver as _qp_solver

    if not _qp_solver.available():
        raise RuntimeError(
            "An L1 boundary requested qp_solver='proxsuite' but the proxsuite "
            "backend is not importable. Install it (pip install proxsuite) or "
            "remove the qp_solver param to use the default box-clamp fusion."
        )

    from dam.guard.aggregators.motion_qp import motion_qp_aggregator

    motion_guard._clamp_aggregator = motion_qp_aggregator


def _resolve_cb_layer(ncfg: Any, layer_str: str, cb_reg: Any) -> str:
    if not ncfg.callback:
        return layer_str
    try:
        fn = cb_reg.get(ncfg.callback)
        return getattr(fn, "_cb_layer", layer_str)
    except KeyError:
        return layer_str


def _register_guard_if_new(
    guard_kind: str,
    ncfg: Any,
    cb_layer: str,
    guards_by_kind: dict[str, Any],
    guard_reg: Any,
) -> None:
    if guard_kind in guards_by_kind:
        return
    from dam.decorators import guard as guard_decorator

    guard_cls = guard_reg.get(guard_kind)
    if guard_cls and (guard_kind != "execution" or ncfg.callback is not None):
        decorated_cls = guard_decorator(cb_layer)(guard_cls)
        instance = decorated_cls()
        instance.set_name(guard_kind)
        instance._guard_kind = guard_kind
        guards_by_kind[guard_kind] = instance


def _build_boundary_node(ncfg: Any, guard_kind: str, config: StackfileConfig) -> Any:
    from dam.boundary.callbacks._registry import normalize_unit_params
    from dam.boundary.constraint import BoundaryConstraint
    from dam.boundary.node import BoundaryNode

    params = normalize_unit_params(ncfg.callback or "", ncfg.params)

    if "device" not in params and config.policy and config.policy.device:
        params["device"] = config.policy.device

    extra = ncfg.model_extra or {}
    if "max_speed" in extra and "max_speed" not in params:
        params["max_speed"] = extra["max_speed"]

    constraint = BoundaryConstraint(
        params=params,
        callback=ncfg.callback,
    )
    timeout_sec = ncfg.timeout_sec
    if ncfg.callback == "task_gripper_command_guard" and timeout_sec is not None:
        logger.warning(
            "Ignoring timeout_sec=%s for task_gripper_command_guard node '%s': "
            "gripper phases advance explicitly and do not have a universal dwell deadline",
            timeout_sec,
            ncfg.node_id,
        )
        timeout_sec = None

    return BoundaryNode(
        node_id=ncfg.node_id,
        constraint=constraint,
        fallback=ncfg.fallback or config.safety.no_task_behavior,
        timeout_sec=timeout_sec,
        warn_frames=max(1, int(ncfg.warn_frames)),
    )


def _make_container(bcfg: Any, nodes: list[Any]) -> Any:
    from dam.boundary.list_container import ListContainer
    from dam.boundary.single import SingleNodeContainer

    if bcfg.type == "single":
        return SingleNodeContainer(nodes[0])
    if bcfg.type == "list":
        return ListContainer(nodes, loop=bcfg.loop)
    raise ValueError(f"Unsupported container type '{bcfg.type}' (graph requires Python setup)")
