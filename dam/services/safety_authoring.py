"""Additive safety composition and conservative, offline wiring diagnostics.

No runtime or hardware is constructed here. A valid compilation only establishes
that declarations are complete; it never attests sensor health or solver readiness.
"""

from __future__ import annotations

import contextlib
import copy
import inspect
import math
import re
from typing import Any

import yaml

from dam.config.safety_plan import SafetyDraftRequest, SafetyPlan
from dam.config.schema import StackfileConfig

_LAYER_GUARDS = {"L0": "ood", "L1": "motion", "L2": "execution", "L3": "hardware"}
_JOINT_CALLBACKS = {
    "joint_position_limits",
    "joint_velocity_limit",
    "joint_acceleration_limit",
    "task_joint_speed_limit",
    "ee_velocity_limit",
    "workspace",
    "keep_out_zone",
    "orientation_limit",
    "task_gripper_command_guard",
}
_SPATIAL_CALLBACKS = {
    "workspace",
    "keep_out_zone",
    "orientation_limit",
    "ee_velocity_limit",
    "task_workspace_bounds",
    "task_gripper_command_guard",
}
_CHANNELS = {
    "temperature_limit": "temperature",
    "current_limit": "current",
    "voltage_limit": "voltage",
    "force_torque_limit": "force_torque",
}
_EXPLICIT_PARAMS = {
    "joint_position_limits": ("lower", "upper"),
    "joint_velocity_limit": ("max_velocities",),
    "joint_acceleration_limit": ("max_acceleration",),
    "workspace": ("bounds",),
    "task_workspace_bounds": ("bounds",),
    "keep_out_zone": ("spheres",),
}


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _finite(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite_number(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return True


def _declared_inputs(config: StackfileConfig, plan: SafetyPlan) -> tuple[set[str], list[str]]:
    observed = set(plan.contract.observation_keys)
    names: list[str] = []
    hardware = config.hardware
    if hardware:
        names = list(hardware.joint_names or [])
        if not names and hardware.preset:
            from dam.preset.registry import get_preset

            with contextlib.suppress(KeyError, ValueError):
                names = list(get_preset(hardware.preset).joint_names)
        for key, source in (hardware.sources or {}).items():
            if source.type in {"motor", "lerobot"}:
                observed.add("joints")
            elif source.type in {"camera", "opencv"}:
                observed.add("images")
            else:
                observed.add(key)
        for key, interface in (hardware.interfaces or {}).items():
            if "robot_telemetry" in interface.capabilities:
                observed.add(key)
    return observed, names


def _has_declared_fk(config: StackfileConfig) -> bool:
    """Check declared capability only, without building a solver or opening URDFs."""
    from dam.solver.builtin import register_all
    from dam.solver.registry import get_global_solver_registry

    register_all()
    registry = get_global_solver_registry()
    from dam.runtime._stackfile_builder import _preset_asset, _preset_solver_configs

    declarations: dict[str, Any] = {**_preset_solver_configs(config), **config.solvers}
    asset = _preset_asset(config)
    for name, spec in declarations.items():
        if hasattr(spec, "model_dump"):
            spec = spec.model_dump()
        if not isinstance(spec, dict):
            continue
        implementation = spec.get("type") or name
        capabilities = spec.get("capabilities")
        if capabilities is None and implementation == "pinocchio_kinematics":
            capabilities = ["fk", "ik"]
        # The factory registry is read only here; never construct a solver.
        factory = registry._factories.get(implementation)
        if factory is None:
            continue
        if capabilities is None:
            capabilities = factory[1]
        if "fk" in (capabilities or []) and "fk" in factory[1]:
            if implementation == "pinocchio_kinematics":
                params = spec.get("params") or {}
                if not params.get("asset_path") and not (asset and asset.get("path")):
                    continue
            return True
    return False


def compile_plan(plan: SafetyPlan) -> dict[str, Any]:
    """Compose without overrides; return diagnostics for every declared boundary."""
    from dam.api import _RESERVED_POOL_KEYS, _register_builtins
    from dam.registry.callback import get_global_registry
    from dam.registry.guard import get_guard_registry
    from dam.runtime import builtin_contexts  # Registers builtin fallback classes only.
    from dam.runtime.context import get_context_class

    _register_builtins()
    data = yaml.safe_load(plan.base_yaml)
    if not isinstance(data, dict):
        raise ValueError("base_yaml must contain a Stackfile mapping")
    data = copy.deepcopy(data)
    raw_finite = _finite(data) and _finite(
        [component.model_dump() for component in plan.components]
    )
    config = StackfileConfig.model_validate(data)
    if plan.task not in config.tasks:
        raise ValueError(f"Explicit target task {plan.task!r} does not exist in base_yaml")
    boundaries = data.setdefault("boundaries", {})
    task_boundaries = data["tasks"][plan.task].setdefault("boundaries", [])
    always = list(config.safety.always_active_list())
    sources = {name: {"component": "base", "kind": "base"} for name in boundaries}
    for component in plan.components:
        for name, boundary in component.boundaries.items():
            if name in boundaries:
                raise ValueError(
                    f"Boundary collision: {name!r}; existing protection cannot be overridden"
                )
            boundaries[name] = boundary.model_dump(mode="json", exclude_defaults=True)
            sources[name] = {"component": component.name, "kind": component.kind}
            target = always if component.kind == "baseline" else task_boundaries
            if name not in target:
                target.append(name)
    data.setdefault("safety", {})["always_active"] = always
    config = StackfileConfig.model_validate(data)
    active = set(always + config.tasks[plan.task].boundaries)
    diagnostics: list[dict[str, str]] = []

    def diagnostic(boundary: str, status: str, code: str, message: str) -> None:
        diagnostics.append(
            {"boundary": boundary, "status": status, "code": code, "message": message}
        )

    if not raw_finite:
        diagnostic(
            "*",
            "error",
            "finite",
            "Stackfile and component values must be finite before serialization",
        )
    declared_observations = plan.contract.observation_keys
    reserved_observations = set(declared_observations) & _RESERVED_POOL_KEYS
    if reserved_observations or any(not key.strip() for key in declared_observations):
        diagnostic(
            "*",
            "error",
            "observation_contract",
            f"Observation keys must be nonblank and cannot override runtime inputs: {sorted(reserved_observations)}",
        )
    if get_context_class(config.safety.no_task_behavior) is None:
        diagnostic(
            "*",
            "missing",
            "fallback",
            f"Default fallback {config.safety.no_task_behavior!r} is not a registered Context",
        )
    for fallback_name, fallback_config in config.fallbacks.items():
        if get_context_class(fallback_name) is None:
            diagnostic(
                "*",
                "missing",
                "fallback",
                f"Configured fallback {fallback_name!r} is not a registered Context; fallback names are not aliases",
            )
        if fallback_config.escalate_to and get_context_class(fallback_config.escalate_to) is None:
            diagnostic(
                "*",
                "missing",
                "fallback",
                f"Escalation fallback {fallback_config.escalate_to!r} is not a registered Context",
            )
    if not active:
        diagnostic("*", "missing", "empty_task", "Selected task has no active safety boundaries")
    for name in sorted(active - set(config.boundaries)):
        diagnostic(name, "missing", "boundary", "Active boundary is not defined")
    if config.safety.enforcement_mode.value != "enforce":
        diagnostic("*", "error", "enforcement", "Safety plans require enforce mode")
    observed, joint_names = _declared_inputs(config, plan)
    hardware = config.hardware
    layout = hardware.action_layout if hardware else []
    if hardware and hardware.preset and not layout:
        from dam.preset.registry import get_preset

        with contextlib.suppress(KeyError, ValueError):
            layout = get_preset(hardware.preset).action_layout
    action_keys: list[str] = []
    for segment in layout:
        keys = segment.get("keys")
        if (
            not isinstance(keys, list)
            or not keys
            or any(not isinstance(key, str) or not key.strip() for key in keys)
        ):
            diagnostic(
                "*",
                "error",
                "action_layout",
                "Each action segment requires a nonempty list of nonblank string keys",
            )
            continue
        action_keys.extend(keys)
    if joint_names and (
        any(not name.strip() for name in joint_names) or len(joint_names) != len(set(joint_names))
    ):
        diagnostic("*", "error", "joint_names", "Hardware joint names must be unique and nonblank")
    if action_keys and action_keys != joint_names and not plan.contract.action_units:
        diagnostic(
            "*", "missing", "action_units", "Declare units for every command-space action key"
        )
    if plan.contract.action_units and any(not unit.strip() for unit in plan.contract.action_units):
        diagnostic("*", "missing", "action_units", "Every action unit must be explicit")
    if plan.contract.joint_names is not None and plan.contract.joint_names != joint_names:
        diagnostic("*", "error", "joint_order", "Declared joint order differs from hardware/preset")
    if action_keys and len(action_keys) != len(set(action_keys)):
        diagnostic("*", "error", "action_layout", "Action keys must be unique")
    if plan.contract.action_units is not None and len(plan.contract.action_units) != len(
        action_keys or joint_names
    ):
        diagnostic(
            "*", "error", "units_dimension", "Action units count differs from action dimension"
        )
    # The builder instantiates guards from callbacks automatically. The guards
    # list only applies overrides; flag declarations the builder would ignore.
    if config.guards and not isinstance(config.guards, list):
        diagnostic(
            "*",
            "error",
            "guard_declaration",
            "Guard overrides must be a list of layer-to-kind mappings",
        )
    for item in config.guards if isinstance(config.guards, list) else []:
        if not isinstance(item, dict):
            diagnostic(
                "*",
                "error",
                "guard_declaration",
                "Guard string entries are ignored by runtime overrides",
            )
            continue
        routes = [(layer, kind) for layer, kind in item.items() if layer in _LAYER_GUARDS]
        if len(routes) != 1 or _LAYER_GUARDS.get(routes[0][0]) != routes[0][1]:
            diagnostic(
                "*",
                "error",
                "guard_declaration",
                "Guard override must map exactly one layer to its effective guard kind",
            )
        if item.get("lane", "fast") not in {"fast", "slow"}:
            diagnostic("*", "error", "guard_lane", "Guard lane must be fast or slow")
        if not _finite(item):
            diagnostic("*", "error", "finite_guard", "Guard overrides must be finite")
    if not _finite(config.safety.model_dump(mode="json")):
        diagnostic(
            "*",
            "error",
            "finite_safety",
            "Safety frequencies, budgets and age limits must be finite",
        )
    registry = get_global_registry()
    for name, container in config.boundaries.items():
        before = len(diagnostics)
        if container.type == "graph":
            diagnostic(
                name,
                "error",
                "container",
                "Graph containers require Python setup and cannot be built from a Stackfile",
            )
        if container.type == "single" and len(container.nodes) != 1:
            diagnostic(
                name,
                "error",
                "container",
                "Single containers require exactly one rule; additional nodes would be ignored",
            )
        if name not in active:
            diagnostic(name, "inactive", "task", "Defined but not active in the selected task")
            continue
        if not container.nodes:
            diagnostic(name, "error", "empty", "Boundary contains no rules")
        for node in container.nodes:
            if node.fallback and get_context_class(node.fallback) is None:
                diagnostic(
                    name,
                    "missing",
                    "fallback",
                    f"Node fallback {node.fallback!r} is not a registered Context",
                )
            callback = node.callback or ""
            try:
                fn = registry.get(callback)
            except KeyError:
                diagnostic(name, "missing", "callback", f"Callback {callback!r} is not registered")
                continue
            layer = getattr(fn, "_cb_layer", None)
            layer = getattr(layer, "name", layer)
            if layer != container.layer or layer not in _LAYER_GUARDS:
                diagnostic(
                    name, "error", "layer", "Callback and boundary guard layers do not match"
                )
            elif get_guard_registry().get(_LAYER_GUARDS[layer]) is None:
                diagnostic(name, "missing", "guard", f"Guard for {layer} is not registered")
            if not _finite(node.model_dump(mode="json")):
                diagnostic(
                    name, "error", "finite_node", "Node timings and parameters must be finite"
                )
            if not _finite(node.params):
                diagnostic(name, "error", "finite", "Parameters contain NaN or infinity")
            params = inspect.signature(fn).parameters
            injected = set(node.params) & (
                _RESERVED_POOL_KEYS | {"ood_context", "host_health", "camera_shapes"}
            )
            if injected:
                diagnostic(
                    name,
                    "error",
                    "runtime_parameter",
                    f"Rule must not override runtime inputs: {sorted(injected)}",
                )
            unknown = set(node.params) - set(params) - {"use_degrees"}
            if unknown and not any(p.kind == p.VAR_KEYWORD for p in params.values()):
                diagnostic(
                    name,
                    "error",
                    "unknown_parameter",
                    f"Unrecognized callback parameters: {sorted(unknown)}",
                )
            if "use_degrees" in node.params and not isinstance(node.params["use_degrees"], bool):
                diagnostic(name, "error", "units", "use_degrees must be a boolean")
            elif node.params.get("use_degrees") and not getattr(fn, "_cb_unit_params", ()):
                diagnostic(
                    name,
                    "error",
                    "units",
                    "This callback does not declare degree-convertible parameters; use_degrees would be ignored",
                )
            if node.model_extra:
                diagnostic(name, "error", "node_fields", "Rule parameters must be inside params")
            if callback == "task_joint_speed_limit" and "joint_velocities" not in observed:
                diagnostic(
                    name,
                    "missing",
                    "observation",
                    "Required joint_velocities observation is not declared",
                )
            if callback == "ood_detector" and "images" not in observed:
                diagnostic(
                    name, "missing", "observation", "Required images observation is not declared"
                )
            known_obs = (
                _JOINT_CALLBACKS
                | _SPATIAL_CALLBACKS
                | set(_CHANNELS)
                | {"hardware_watchdog", "host_health_limit", "ood_detector"}
            )
            if "obs" in params and callback not in known_obs:
                diagnostic(
                    name,
                    "missing",
                    "opaque_observation",
                    "Custom callback reads obs; its observation requirements cannot be determined statically",
                )
            for key in _EXPLICIT_PARAMS.get(callback, ()):
                if node.params.get(key) is None or node.params.get(key) == []:
                    diagnostic(name, "missing", "parameter", f"Explicit {key} is required")
            for key, parameter in params.items():
                if parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL):
                    continue
                if key in _RESERVED_POOL_KEYS or key in {
                    "ood_context",
                    "host_health",
                    "camera_shapes",
                }:
                    continue
                if (
                    key not in node.params
                    and parameter.default is inspect.Parameter.empty
                    and key not in observed
                ):
                    diagnostic(
                        name,
                        "missing",
                        "input",
                        f"Required parameter/observation {key!r} is not declared",
                    )
            if callback in _JOINT_CALLBACKS:
                if "joints" not in observed:
                    diagnostic(
                        name,
                        "missing",
                        "observation",
                        "Required joints observation is not declared",
                    )
                if not joint_names:
                    diagnostic(
                        name, "missing", "joint_names", "Joint names and dimension are not declared"
                    )
                if action_keys and action_keys != joint_names:
                    diagnostic(
                        name,
                        "error",
                        "action_semantics",
                        "Joint guards require action keys in joint order",
                    )
                if not hardware or (
                    hardware.degrees_mode is None
                    and not any(
                        source.degrees_mode is not None and source.type in {"motor", "lerobot"}
                        for source in (hardware.sources or {}).values()
                    )
                ):
                    diagnostic(
                        name, "missing", "units", "Declare native joint units via degrees_mode"
                    )
                for key in ("lower", "upper", "max_velocities", "max_acceleration"):
                    value = node.params.get(key)
                    if isinstance(value, list) and len(value) != len(joint_names):
                        diagnostic(
                            name, "error", "dimension", f"{key} length differs from joint dimension"
                        )
                if plan.contract.action_units:
                    expected = "deg" if hardware and hardware.motor_degrees_mode() else "rad"
                    if any(unit != expected for unit in plan.contract.action_units):
                        diagnostic(
                            name,
                            "error",
                            "units",
                            f"Action units disagree with native joint mode ({expected})",
                        )
            if callback in _SPATIAL_CALLBACKS:
                if not _has_declared_fk(config):
                    diagnostic(name, "missing", "fk", "No FK-capable solver is declared")
                if not (plan.contract.frame and plan.contract.frame.strip()):
                    diagnostic(
                        name,
                        "missing",
                        "frame",
                        "Confirm the solver-native coordinate frame; no frame conversion is performed",
                    )
            if callback in _CHANNELS:
                channel = str(node.params.get("channel", _CHANNELS[callback]))
                if channel not in observed:
                    diagnostic(
                        name,
                        "missing",
                        "observation",
                        f"Required {channel!r} observation is not declared",
                    )
            if callback in {"joint_velocity_limit", "joint_acceleration_limit"}:
                key = "max_velocities" if callback == "joint_velocity_limit" else "max_acceleration"
                value = node.params.get(key)
                values = value if isinstance(value, list) else [value]
                if any(
                    not isinstance(item, (int, float)) or isinstance(item, bool) or item <= 0
                    for item in values
                ):
                    diagnostic(name, "error", "limit", f"{key} must contain positive finite limits")
            if callback == "ee_velocity_limit":
                limit = node.params.get("max_ee_velocity", params["max_ee_velocity"].default)
                if not _finite_number(limit) or limit <= 0:
                    diagnostic(
                        name,
                        "error",
                        "limit",
                        "max_ee_velocity must be a positive finite scalar in m/s",
                    )
            if callback == "joint_position_limits":
                lower, upper = node.params.get("lower", []), node.params.get("upper", [])
                if (
                    not isinstance(lower, list)
                    or not isinstance(upper, list)
                    or len(lower) != len(upper)
                ):
                    diagnostic(
                        name, "error", "bounds", "Joint bounds must have matching dimensions"
                    )
                elif any(
                    not _finite_number(lo) or not _finite_number(hi) or lo >= hi
                    for lo, hi in zip(lower, upper, strict=True)
                ):
                    diagnostic(
                        name,
                        "error",
                        "bounds",
                        "Each lower joint bound must be below its upper bound",
                    )
            if callback in {"workspace", "task_workspace_bounds"} and not _valid_box(
                node.params.get("bounds")
            ):
                diagnostic(
                    name,
                    "error",
                    "bounds",
                    "Workspace requires three finite increasing [min,max] pairs in metres",
                )
            if callback == "task_gripper_command_guard":
                for zone_key in ("zone", "pick_zone", "place_zone"):
                    if node.params.get(zone_key) is not None and not _valid_box(
                        node.params[zone_key]
                    ):
                        diagnostic(
                            name,
                            "error",
                            "bounds",
                            f"{zone_key} requires three finite increasing [min,max] pairs in metres",
                        )
            if callback in {"workspace", "keep_out_zone", "orientation_limit"}:
                alpha = node.params.get("cbf_alpha", params["cbf_alpha"].default)
                if not _finite_number(alpha) or alpha <= 0:
                    diagnostic(
                        name, "error", "cbf_alpha", "cbf_alpha must be a positive finite scalar"
                    )
            if callback == "orientation_limit":
                for axis_key in ("reference_axis", "tool_axis"):
                    axis = node.params.get(axis_key)
                    if axis is not None and not _valid_axis(axis):
                        diagnostic(
                            name,
                            "error",
                            "axis",
                            f"{axis_key} requires three finite numbers with finite norm at least 1e-12; smaller axes are skipped by the callback",
                        )
                angle = node.params.get("max_tilt_deg", params["max_tilt_deg"].default)
                if not _finite_number(angle) or not 0 <= angle <= 180:
                    diagnostic(
                        name,
                        "error",
                        "angle",
                        "max_tilt_deg must be a finite angle between 0 and 180 degrees",
                    )
            if callback == "keep_out_zone":
                spheres = node.params.get("spheres")
                if (
                    not isinstance(spheres, list)
                    or not spheres
                    or any(
                        not isinstance(sphere, list)
                        or len(sphere) != 4
                        or not all(_finite_number(v) for v in sphere)
                        or sphere[3] <= 0
                        for sphere in spheres
                    )
                ):
                    diagnostic(
                        name,
                        "error",
                        "spheres",
                        "Keep-out spheres need finite x/y/z/radius in metres and positive radius",
                    )
        if len(diagnostics) == before:
            diagnostic(
                name,
                "declaration-only",
                "declared",
                "Active declarations are complete; runtime inputs, solver readiness and sensor health are unverified",
            )
    for conflict in _intersection_conflicts(config, active):
        diagnostic("*", "error", "conflict", conflict)
    return {
        "yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        "task": plan.task,
        "valid": not any(item["status"] in {"missing", "error"} for item in diagnostics),
        "sources": sources,
        "diagnostics": diagnostics,
        "runtime_verified": False,
    }


def _intersection_conflicts(config: StackfileConfig, active: set[str]) -> list[str]:
    """Detect provably empty intersections of concurrently active simple boxes.

    Stateful list/graph nodes are alternatives over time, so we must not falsely
    intersect their phase-specific bounds. Runtime still evaluates those rules.
    """
    from dam.boundary.callbacks._registry import normalize_unit_params

    groups: dict[str, list[tuple[str, list[list[float]]]]] = {"joint": [], "workspace": []}
    for name in sorted(active):
        container = config.boundaries.get(name)
        if not container or container.type != "single" or len(container.nodes) != 1:
            continue
        node = container.nodes[0]
        if node.callback == "workspace" and _valid_box(node.params.get("bounds")):
            groups["workspace"].append((name, node.params["bounds"]))
        if node.callback == "joint_position_limits":
            lower, upper = node.params.get("lower"), node.params.get("upper")
            if (
                not isinstance(lower, list)
                or not isinstance(upper, list)
                or len(lower) != len(upper)
            ):
                continue
            if not all(_finite_number(value) for value in lower + upper):
                continue
            normalized = normalize_unit_params(node.callback, node.params)
            groups["joint"].append(
                (
                    name,
                    [
                        [lo, hi]
                        for lo, hi in zip(normalized["lower"], normalized["upper"], strict=True)
                    ],
                )
            )
    conflicts: list[str] = []
    for kind, boxes in groups.items():
        if len(boxes) < 2:
            continue
        dimension = len(boxes[0][1])
        if not all(len(box) == dimension for _, box in boxes):
            continue
        for axis in range(dimension):
            lower = max(box[axis][0] for _, box in boxes)
            upper = min(box[axis][1] for _, box in boxes)
            if lower > upper:
                conflicts.append(
                    f"Active {kind} constraints have no common interval on axis {axis}: {', '.join(name for name, _ in boxes)}"
                )
                break
    return conflicts


def _valid_axis(axis: Any) -> bool:
    if (
        not isinstance(axis, list)
        or len(axis) != 3
        or not all(_finite_number(value) for value in axis)
    ):
        return False
    # Mirror the callback's ordinary Euclidean norm, including its overflow and
    # small-axis skip threshold, without executing the runtime callback.
    norm_squared = sum(float(value) * float(value) for value in axis)
    return math.isfinite(norm_squared) and norm_squared >= 1e-24


def _valid_box(bounds: Any) -> bool:
    return (
        isinstance(bounds, list)
        and len(bounds) == 3
        and all(
            isinstance(pair, list)
            and len(pair) == 2
            and all(_finite_number(v) for v in pair)
            and pair[0] < pair[1]
            for pair in bounds
        )
    )


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def draft_plan(request: SafetyDraftRequest) -> dict[str, Any]:
    """Parse a deliberately small bilingual grammar; unknown clauses stay unresolved."""
    components: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for index, clause in enumerate(re.split(r"[;；\n。]+", request.text)):
        clause = clause.strip()
        if not clause:
            continue
        text = clause.lower().replace("²", "^2")
        if re.search(
            r"\b(if|when|near|unless|except|not|only)\b|靠近|當|如果|除非|不要|僅|只在", text
        ):
            unresolved.append(
                f"Conditional or negative rule needs explicit manual authoring: {clause}"
            )
            continue
        callback: str | None = None
        params: dict[str, Any] = {}
        kind = "mode"
        velocity = re.fullmatch(
            rf"(?:joint\s+(velocity|speed|acceleration)|關節\s*(速度|加速度))\s*(?:limit|上限)?\s*(?:<=|≤|=|:|：|不超過)?\s*({_NUMBER})\s*(rad/s(?:\^2)?|deg/s(?:\^2)?|度/秒(?:\^2)?)",
            text,
        )
        if velocity:
            label = velocity.group(1) or velocity.group(2)
            number = float(velocity.group(3))
            unit = velocity.group(4)
            acceleration = label in {"acceleration", "加速度"}
            if not math.isfinite(number) or number <= 0 or acceleration != unit.endswith("^2"):
                unresolved.append(
                    f"Positive limit and matching speed/acceleration units required: {clause}"
                )
                continue
            callback = "joint_acceleration_limit" if acceleration else "joint_velocity_limit"
            params = {"max_acceleration" if acceleration else "max_velocities": number}
            if unit.startswith(("deg", "度")):
                params["use_degrees"] = True
        elif re.match(r"^(workspace|工作區)\b|^工作區", text):
            remainder = re.sub(r"^(workspace|工作區)\s*[:：]?\s*", "", text)
            axis_pattern = rf"([xyz])\s*(?:=|:|：)?\s*\[\s*({_NUMBER})\s*,\s*({_NUMBER})\s*\]\s*m"
            matches = list(re.finditer(axis_pattern, remainder))
            leftover = re.sub(axis_pattern, "", remainder).strip(" ,，")
            axes = {
                match.group(1): [float(match.group(2)), float(match.group(3))] for match in matches
            }
            if leftover or len(matches) != 3 or set(axes) != {"x", "y", "z"}:
                unresolved.append(f"Workspace needs x[min,max]m y[min,max]m z[min,max]m: {clause}")
                continue
            if not request.frame or not request.frame.strip():
                unresolved.append("Workspace requires an explicitly confirmed solver-native frame")
                continue
            bounds = [axes[axis] for axis in "xyz"]
            if not _valid_box(bounds):
                unresolved.append(f"Workspace bounds must increase: {clause}")
                continue
            callback, params, kind = "workspace", {"bounds": bounds}, "scene"
        if callback:
            name = f"draft_{callback}_{index + 1}"
            components.append(
                {
                    "name": name,
                    "kind": kind,
                    "boundaries": {
                        name: {
                            "type": "single",
                            "layer": "L1",
                            "nodes": [{"callback": callback, "params": params}],
                        },
                    },
                }
            )
        else:
            unresolved.append(f"Unrecognized rule; specify quantities, units and scope: {clause}")
    if not components and not unresolved:
        unresolved.append("Provide at least one explicit rule with quantities and units")
    if unresolved:
        components = []
    return {
        "components": components,
        "unresolved": unresolved,
        "explanation": (
            "No components were generated because some clauses remain unresolved. Resolve the whole request before adding rules. "
            if unresolved
            else ""
        )
        + "Draft only. Review every rule and unresolved clause, then compile with your hardware/input contract. Coordinate values use the declared solver-native frame; no transform is applied.",
        "runtime_verified": False,
    }
