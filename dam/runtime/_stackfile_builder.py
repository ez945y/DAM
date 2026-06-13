"""Stackfile → GuardRuntime construction helpers.

Extracted from ``guard_runtime.py`` to keep the runtime orchestrator focused
on cycle execution.  All functions here are pure construction logic — they
build a GuardRuntime instance and return it, with zero runtime coupling.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.config.schema import StackfileConfig

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

    solvers = _init_solvers(config)
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
        solvers=solvers,
        boundary_to_kind=boundary_to_kind,
        frame_hub=frame_hub,
        default_fallback=config.safety.no_task_behavior,
        slow_lane_config=config.safety.slow_lane,
    )

    _apply_guard_overrides(config, guards_by_kind, runtime)
    runtime._ctx_sm.fallbacks_config = dict(config.fallbacks)

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
        lane_override = item.get("lane")
        if lane_override is not None:
            lane = str(lane_override).lower()
            if lane not in ("fast", "slow"):
                raise ValueError(
                    f"guard '{kind_value}': lane must be 'fast' or 'slow', got '{lane_override}'"
                )
            inst._lane = lane
            logger.info("Stackfile override: guard '%s' lane=%s", kind_value, lane)
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


def _register_builtin_solver_factories() -> None:
    from dam.solver.registry import get_global_solver_registry

    registry = get_global_solver_registry()

    def pinocchio_kinematics(params: Mapping[str, Any]) -> Any:
        from dam.kinematics.resolver import KinematicsResolver

        urdf_path = params.get("asset_path")
        if not urdf_path:
            raise ValueError("pinocchio_kinematics solver requires params.asset_ref='urdf'")
        return KinematicsResolver(
            str(urdf_path),
            controlled_joints=params.get("controlled_joints"),
            ee_link_name=str(params.get("ee_link_name", "gripper_link")),
            observation_joint_names=params.get("observation_joint_names"),
        )

    with contextlib.suppress(ValueError):
        registry.register_factory(
            "pinocchio_kinematics",
            pinocchio_kinematics,
            capabilities=("kinematics", "fk", "ik"),
        )


def _preset_joint_names(config: StackfileConfig) -> list[str] | None:
    if not (config.hardware and config.hardware.preset):
        return None
    try:
        from dam.preset.registry import get_preset

        return get_preset(config.hardware.preset).joint_names or None
    except Exception:  # noqa: BLE001 — unknown preset: keep positional fallback
        logger.warning(
            "GuardRuntime: preset '%s' not found; solver will align joints positionally.",
            config.hardware.preset,
        )
        return None


def _preset_asset(config: StackfileConfig, asset_key: str) -> str | None:
    if not (config.hardware and config.hardware.preset):
        return None
    try:
        from dam.preset.registry import get_preset

        return get_preset(config.hardware.preset).asset_path(asset_key)
    except Exception:  # noqa: BLE001
        logger.warning(
            "GuardRuntime: preset '%s' not found; cannot resolve solver asset '%s'.",
            config.hardware.preset,
            asset_key,
        )
        return None


def _preset_solver_configs(config: StackfileConfig) -> dict[str, Any]:
    if not (config.hardware and config.hardware.preset):
        return {}
    try:
        from dam.preset.registry import get_preset

        return dict(get_preset(config.hardware.preset).solvers or {})
    except Exception:  # noqa: BLE001
        logger.warning(
            "GuardRuntime: preset '%s' not found; cannot load preset solvers.",
            config.hardware.preset,
        )
        return {}


def _init_solvers(config: StackfileConfig) -> dict[str, Any]:
    _register_builtin_solver_factories()
    from dam.solver.registry import get_global_solver_registry

    registry = get_global_solver_registry()
    solvers: dict[str, Any] = {}

    solver_configs: dict[str, Any] = {**_preset_solver_configs(config), **config.solvers}

    for name, scfg in solver_configs.items():
        if isinstance(scfg, dict):
            solver_type = str(scfg.get("type", ""))
            capabilities = scfg.get("capabilities")
            params = dict(scfg.get("params") or {})
        else:
            solver_type = scfg.type
            capabilities = scfg.capabilities
            params = dict(scfg.params or {})
        asset_ref = params.get("asset_ref") or params.get("asset")
        if asset_ref and "asset_path" not in params:
            asset_path = _preset_asset(config, str(asset_ref))
            if asset_path:
                params["asset_path"] = asset_path
        if "observation_joint_names" not in params:
            preset_names = _preset_joint_names(config)
            if preset_names:
                params["observation_joint_names"] = preset_names
        try:
            solver = registry.build(
                name,
                solver_type,
                params,
                capabilities=capabilities,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "GuardRuntime: failed to init solver '%s' (%s): %s",
                name,
                solver_type,
                e,
            )
            continue
        solvers[name] = solver

    return solvers


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
    """Validate that required backends are available for configured guards."""
    motion_guard = guards_by_kind.get("motion")
    if motion_guard is None:
        return

    # QP is mandatory for L1 — check proxsuite at startup when L1 boundaries exist.
    has_l1 = any(getattr(bcfg, "layer", None) == "L1" for bcfg in config.boundaries.values())
    if not has_l1:
        return

    from dam.runtime import qp_solver as _qp_solver

    if not _qp_solver.available():
        raise RuntimeError(
            "L1 boundaries require the proxsuite QP solver. Install it: pip install proxsuite"
        )


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
