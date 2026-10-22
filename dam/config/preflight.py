"""Configuration loading and static preflight without runtime/resource creation."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import ValidationError

from dam.config.loader import StackfileLoader
from dam.config.schema import FallbackConfig, StackfileConfig
from dam.errors import ConfigurationError
from dam.types.action import action_segment_size

ConfigurationSource = str | Path | Mapping[str, Any] | StackfileConfig


def load_configuration(source: ConfigurationSource) -> StackfileConfig:
    """Return an owned, validated configuration for any public input form."""
    if isinstance(source, (str, Path)):
        return StackfileLoader.load(str(source))
    if isinstance(source, StackfileConfig):
        return StackfileConfig.model_validate(copy.deepcopy(source.model_dump()))
    if isinstance(source, Mapping):
        return StackfileConfig.model_validate(copy.deepcopy(dict(source)))
    raise TypeError("configuration must be a path, mapping or StackfileConfig")


@dataclass(frozen=True)
class PreflightIssue:
    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class PreflightReport:
    issues: tuple[PreflightIssue, ...]
    task: str | None = None
    required_observations: tuple[str, ...] = ()
    joint_names: tuple[str, ...] = ()
    action_layout: tuple[dict[str, Any], ...] = ()
    command_size: int | None = None
    command_space: bool = False
    joint_units: str | None = None

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, **asdict(self)}

    def raise_for_errors(self) -> None:
        if not self.valid:
            raise ConfigurationError(
                "; ".join(f"{i.field}: {i.message}" for i in self.issues if i.severity == "error")
            )


def preflight(
    source: ConfigurationSource,
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
    solvers: Mapping[str, Any] | None = None,
    safe_action: Any = "hold",
) -> PreflightReport:
    """Inspect schema and static wiring; never invoke callbacks or factories.

    Registration and preset/config file reads are allowed. No runtime, device,
    worker, recorder or solver instance is created. A valid report does not
    certify solver initialization, callback behavior or physical safety.
    """
    from dam.api import (
        _RESERVED_POOL_KEYS,
        _command_array,
        _register_builtins,
        _resolve_action_layout,
    )
    from dam.preset.registry import get_preset
    from dam.registry.callback import get_global_registry
    from dam.runtime import builtin_contexts  # noqa: F401 — registers context classes
    from dam.runtime._stackfile_builder import (
        _preset_solver_configs,
        _register_builtin_solver_factories,
    )
    from dam.runtime.context import get_context_class
    from dam.solver.registry import get_global_solver_registry

    issues: list[PreflightIssue] = []

    def error(field: str, message: str) -> None:
        issues.append(PreflightIssue(field, message))

    try:
        config = load_configuration(source)
    except Exception as exc:
        cause: BaseException | None = exc
        while cause is not None and not isinstance(cause, ValidationError):
            cause = cause.__cause__
        if isinstance(cause, ValidationError):
            for item in cause.errors():
                error(".".join(str(part) for part in item["loc"]) or "configuration", item["msg"])
        else:
            error("configuration", str(exc))
        return PreflightReport(tuple(issues))

    _register_builtins()
    _register_builtin_solver_factories()
    task = task or next(iter(config.tasks), None)
    if task is None or task not in config.tasks:
        error("tasks", f"Unknown or missing task {task!r}; available: {sorted(config.tasks)}")

    hardware = config.hardware
    preset = None
    if hardware and hardware.preset:
        try:
            preset = get_preset(hardware.preset)
        except Exception as exc:
            error("hardware.preset", str(exc))
    names = joint_names
    if names is None:
        names = hardware.joint_names if hardware and hardware.joint_names is not None else None
    if names is None and preset is not None:
        names = preset.joint_names
    if names is None:
        error("hardware.joint_names", "Declare joint_names or a known preset")
        names = []
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        error("hardware.joint_names", "Joint names must be unique nonempty strings")
    mode = (
        degrees_mode
        if degrees_mode is not None
        else (hardware.motor_degrees_mode() if hardware else True)
    )
    if not isinstance(mode, bool):
        error("hardware.degrees_mode", "degrees_mode must be boolean")

    layout = _resolve_action_layout(config)
    widths: list[int] = []
    declared_widths: dict[int, int] = {}
    keys: list[str] = []
    fully_keyed = bool(layout)
    for index, segment in enumerate(layout):
        field = f"hardware.action_layout.{index}"
        try:
            if "keys" not in segment:
                raw_width = segment.get("size", segment.get("dim", segment.get("dimensions")))
                if raw_width is not None and (
                    isinstance(raw_width, bool) or not isinstance(raw_width, int)
                ):
                    raise ValueError("Segment width must be an integer")
            width = action_segment_size(segment)
            if width is None or width <= 0:
                raise ValueError("Declare a positive segment width or keys")
            widths.append(width)
            declared_widths[index] = width
            if "keys" in segment:
                if not isinstance(segment["keys"], (list, tuple)) or any(
                    not isinstance(key, str) or not key for key in segment["keys"]
                ):
                    raise ValueError("keys must be nonempty strings")
                keys.extend(segment["keys"])
            else:
                fully_keyed = False
        except (TypeError, ValueError) as exc:
            error(field, str(exc))
    if len(set(keys)) != len(keys):
        error("hardware.action_layout", "Command keys must be unique")
    size = sum(widths) if layout else len(names)
    command_space = bool(layout) and (not fully_keyed or keys != names)
    if not size:
        error("hardware.action_layout", "Command must contain at least one value")
    if command_space:
        for index, segment in enumerate(layout):
            units = segment.get("units")
            if units is None:
                issues.append(
                    PreflightIssue(
                        f"hardware.action_layout.{index}.units",
                        "Command units are unspecified; caller must establish them",
                        "warning",
                    )
                )
            elif (
                not isinstance(units, list)
                or len(units) != declared_widths.get(index)
                or any(not isinstance(u, str) or not u for u in units)
            ):
                error(
                    f"hardware.action_layout.{index}.units",
                    "units must name each command coordinate",
                )
    if isinstance(safe_action, str):
        if safe_action not in ("hold", "zero") or (command_space and safe_action == "hold"):
            error(
                "safe_action",
                "Command-space actions need an explicit vector or 'zero'; hold is joint-only",
            )
    else:
        try:
            value = _command_array(safe_action)
            if value.ndim != 1 or value.size != size or not np.all(np.isfinite(value)):
                raise ValueError("Fallback must be a finite vector matching command size")
        except (TypeError, ValueError) as exc:
            error("safe_action", str(exc))

    registry = get_global_solver_registry()
    factories = registry.factory_capabilities()
    solver_configs = {**_preset_solver_configs(config), **config.solvers}
    available_solvers = set(solvers or {}) | set(solver_configs)
    for name, spec in solver_configs.items():
        if not isinstance(spec, dict) and not hasattr(spec, "type"):
            error(f"solvers.{name}", "Solver declaration must be a mapping")
            continue
        solver_type = (spec.get("type") if isinstance(spec, dict) else spec.type) or name
        normalized = str(solver_type).strip().lower().replace("-", "_")
        if name not in (solvers or {}) and normalized not in factories:
            error(
                f"solvers.{name}.type",
                f"Unknown factory {solver_type!r}; available: {sorted(factories)}",
            )
    for index, segment in enumerate(layout):
        if "solver" in segment and (
            not isinstance(segment["solver"], str) or segment["solver"] not in available_solvers
        ):
            error(
                f"hardware.action_layout.{index}.solver",
                f"Unknown configured solver {segment['solver']!r}",
            )

    required: set[str] = set()
    referenced_fallbacks = {config.safety.no_task_behavior}
    callbacks = get_global_registry()
    active = list(
        dict.fromkeys(
            [
                *config.safety.always_active_list(),
                *(config.tasks[task].boundaries if task in config.tasks else []),
            ]
        )
    )
    for boundary in active:
        if boundary not in config.boundaries:
            error(f"boundaries.{boundary}", "Referenced boundary does not exist")
            continue
        for index, node in enumerate(config.boundaries[boundary].nodes):
            field = f"boundaries.{boundary}.nodes.{index}"
            if node.callback:
                try:
                    fn = callbacks.get(node.callback)
                    for parameter in inspect.signature(fn).parameters.values():
                        if (
                            parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
                            and parameter.default is parameter.empty
                            and parameter.name not in _RESERVED_POOL_KEYS
                            and parameter.name not in node.params
                        ):
                            required.add(parameter.name)
                except (KeyError, TypeError, ValueError) as exc:
                    error(f"{field}.callback", str(exc))
            if node.fallback and get_context_class(node.fallback) is None:
                error(f"{field}.fallback", f"Unknown fallback {node.fallback!r}")
            if node.fallback:
                referenced_fallbacks.add(node.fallback)

    fallback_specs = {name: FallbackConfig() for name in referenced_fallbacks}
    fallback_specs.update(config.fallbacks)
    for fallback in config.fallbacks.values():
        if fallback.escalate_to:
            fallback_specs.setdefault(fallback.escalate_to, FallbackConfig())
    for name, fallback in fallback_specs.items():
        cls = get_context_class(name)
        if cls is None:
            error(f"fallbacks.{name}", "Unknown fallback context")
            continue
        try:
            inspect.signature(cls).bind(**fallback.params)
        except TypeError as exc:
            error(f"fallbacks.{name}.params", str(exc))
        for setting in ("duration_seconds", "arrival_tol", "wait_seconds", "hysteresis_seconds"):
            if setting in fallback.params and fallback.params[setting] is not None:
                value = fallback.params[setting]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not np.isfinite(value)
                    or value < 0
                ):
                    error(f"fallbacks.{name}.params.{setting}", "Must be finite and nonnegative")
        if fallback.escalate_after_seconds is not None and (
            not np.isfinite(fallback.escalate_after_seconds) or fallback.escalate_after_seconds < 0
        ):
            error(f"fallbacks.{name}.escalate_after_seconds", "Must be finite and nonnegative")
        if command_space and name in ("hold_position", "retreat", "slow_down"):
            error(f"fallbacks.{name}", "Joint-position fallback cannot serve this command layout")
        if name == "retreat":
            target = fallback.params.get("target_positions")
            try:
                arr = np.asarray(target, dtype=np.float64)
                if arr.ndim != 1 or arr.size != size or not np.all(np.isfinite(arr)):
                    raise ValueError(
                        "Retreat requires an explicit finite target matching command size"
                    )
            except (TypeError, ValueError) as exc:
                error(f"fallbacks.{name}.params.target_positions", str(exc))
        if name == "slow_down":
            try:
                scale = float(fallback.params.get("scale", 0.5))
                if not np.isfinite(scale) or not 0 <= scale <= 1:
                    raise ValueError("scale must be finite and in [0, 1]")
            except (TypeError, ValueError) as exc:
                error(f"fallbacks.{name}.params.scale", str(exc))
        if fallback.escalate_to and get_context_class(fallback.escalate_to) is None:
            error(f"fallbacks.{name}.escalate_to", "Unknown escalation context")

    return PreflightReport(
        tuple(issues),
        task,
        tuple(sorted(required)),
        tuple(names),
        tuple(copy.deepcopy(layout)),
        size,
        command_space,
        "degrees" if mode else "radians",
    )
