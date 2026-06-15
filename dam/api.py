"""Public programmatic API for embedding DAM as a library.

``dam.build_runner`` / ``dam.run`` are thin, stable wrappers over
``RuntimeFactory`` so callers don't have to know the internal factory /
registry wiring.  The ``dam`` CLI's ``run`` subcommand is a thin shell over
``dam.run`` — single source of truth.

``dam.Guardrail`` / ``dam.guardrail`` provide a lightweight action-validation
API that doesn't require hardware — ideal for wrapping policy outputs
during IL data collection or offline evaluation.

Heavy dependencies (factory, adapters, torch, …) are imported lazily inside
the functions so ``import dam`` stays cheap.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

import numpy as np

if TYPE_CHECKING:
    from dam.runner.base import BaseRunner
    from dam.types.result import GuardResult

logger = logging.getLogger(__name__)

_DEG2RAD = float(np.pi / 180.0)
_RAD2DEG = float(180.0 / np.pi)


@dataclass(frozen=True)
class RunSummary:
    """Outcome of a :func:`run` invocation."""

    status: str  # RunnerStatus name, e.g. "STOPPED", "EMERGENCY"
    cycles: int
    emergency: bool


def register_callback(
    name: str,
    fn: Callable[..., Any] | None = None,
    *,
    layer: str = "L1",
    category: str = "custom",
    description: str = "",
    params: Mapping[str, str] | None = None,
    unit_params: tuple[str, ...] | list[str] | None = None,
    internal_params: tuple[str, ...] | list[str] | None = None,
) -> Callable[..., Any] | Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a boundary callback from library code.

    Usable either as a direct call or a decorator:

    .. code-block:: python

        def check(...): ...
        dam.register_callback("my_check", check, layer="L2")

        @dam.register_callback("my_other_check", layer="L1")
        def check_other(...): ...

    The callback is added to both the runtime registry and the boundary
    callback catalog, so Stackfiles can reference it by name and tools can
    display its metadata.
    """
    from dam.guard.layer import GuardLayer

    try:
        GuardLayer[layer]
    except KeyError:
        valid = [member.name for member in GuardLayer]
        raise ValueError(f"Unknown callback layer '{layer}'. Valid layers: {valid}") from None

    def decorator(callback_fn: Callable[..., Any]) -> Callable[..., Any]:
        from dam.boundary.callbacks._registry import register_external_callback

        return register_external_callback(
            name=name,
            fn=callback_fn,
            layer=layer,
            category=category,
            description=description,
            params=params,
            unit_params=unit_params,
            internal_params=internal_params,
        )

    if fn is None:
        return decorator
    return decorator(fn)


def register_solver(
    name: str,
    solver: Any,
    *,
    capabilities: tuple[str, ...] | list[str],
    replace: bool = False,
) -> Any:
    """Register a solver instance as a first-class runtime dependency.

    Examples of capabilities: ``"kinematics"``, ``"dynamics"``,
    ``"base_dynamics"``, ``"collision"``. DAM does not prescribe the solver
    object's concrete methods; callbacks request the capability they need.
    """
    from dam.solver.registry import get_global_solver_registry

    return get_global_solver_registry().register(
        name,
        solver,
        capabilities=capabilities,
        replace=replace,
    )


def register_preset(
    name: str,
    *,
    joint_names: list[str],
    asset: dict[str, str] | None = None,
    solvers: dict[str, Any] | None = None,
    action_layout: list[dict[str, Any]] | None = None,
    rename_from: str | None = None,
) -> Any:
    """Register or update a robot preset in DAM's preset registry.

    This is the public library-facing alias for the YAML-backed preset registry:
    callers should use ``dam.register_preset(...)`` together with
    ``dam.register_solver(...)`` / ``dam.register_callback(...)`` when defining
    a custom embodiment from Python.
    """
    from dam.preset import upsert_preset

    return upsert_preset(
        name,
        joint_names=joint_names,
        asset=asset,
        solvers=solvers,
        action_layout=action_layout,
        rename_from=rename_from,
    )


_InterfaceFactory = Callable[[str, Any, Mapping[str, Any]], Any]
_InterfaceDecorator = Callable[[_InterfaceFactory], _InterfaceFactory]


@overload
def _interface_register(
    register_method: str,
    interface_type: str,
    factory: None,
    replace: bool,
) -> _InterfaceDecorator: ...


@overload
def _interface_register(
    register_method: str,
    interface_type: str,
    factory: _InterfaceFactory,
    replace: bool,
) -> _InterfaceFactory: ...


def _interface_register(
    register_method: str,
    interface_type: str,
    factory: _InterfaceFactory | None,
    replace: bool,
) -> _InterfaceFactory | _InterfaceDecorator:
    """Shared dual-form (direct call / decorator) interface registration.

    Like :func:`register_callback`, the name you register under IS the interface
    ``type`` stackfiles reference — telemetry channels may then omit ``type:``
    entirely (the channel key resolves to the registered name).
    """
    from dam.interface.registry import get_global_interface_registry

    def decorator(fn: _InterfaceFactory) -> _InterfaceFactory:
        registry = get_global_interface_registry()
        if register_method == "register_read":
            return registry.register_read(interface_type, fn, replace=replace)
        if register_method == "register_write":
            return registry.register_write(interface_type, fn, replace=replace)
        if register_method == "register_robot_telemetry":
            return registry.register_robot_telemetry(interface_type, fn, replace=replace)
        if register_method == "register_host_telemetry":
            return registry.register_host_telemetry(interface_type, fn, replace=replace)
        raise ValueError(f"Unknown interface registration method '{register_method}'")

    if factory is None:
        return decorator
    return decorator(factory)


def register_read_interface(
    interface_type: str,
    factory: _InterfaceFactory | None = None,
    *,
    replace: bool = False,
) -> _InterfaceFactory | _InterfaceDecorator:
    """Register a user-defined runtime read interface (direct call or decorator).

    The factory receives ``(name, source_config, context)`` and must return an
    object with ``read() -> Observation``. Optional lifecycle methods such as
    ``connect()``, ``verify()``, and ``disconnect()`` are called when present.
    """
    return _interface_register("register_read", interface_type, factory, replace)


def register_write_interface(
    interface_type: str,
    factory: _InterfaceFactory | None = None,
    *,
    replace: bool = False,
) -> _InterfaceFactory | _InterfaceDecorator:
    """Register a user-defined runtime write interface (direct call or decorator).

    The factory receives ``(name, sink_config, context)`` and must return an
    object with ``apply(ValidatedAction)`` or ``write(ValidatedAction)``.
    """
    return _interface_register("register_write", interface_type, factory, replace)


def register_robot_telemetry_interface(
    interface_type: str,
    factory: _InterfaceFactory | None = None,
    *,
    replace: bool = False,
) -> _InterfaceFactory | _InterfaceDecorator:
    """Register a robot telemetry interface (direct call or decorator).

    The returned object should provide robot health/status data, typically via
    ``read() -> Observation`` channels or ``get_hardware_status()``.
    """
    return _interface_register("register_robot_telemetry", interface_type, factory, replace)


def register_host_telemetry_interface(
    interface_type: str,
    factory: _InterfaceFactory | None = None,
    *,
    replace: bool = False,
) -> _InterfaceFactory | _InterfaceDecorator:
    """Register a host telemetry interface (direct call or decorator)."""
    return _interface_register("register_host_telemetry", interface_type, factory, replace)


def _register_builtins() -> None:
    """Register built-in callbacks and guards (idempotent).

    Builtin fallback Contexts (HoldPosition/SlowDown/Retreat/WaitAndRetry/
    EmergencyStop) live in ``dam.runtime.builtin_contexts`` and self-register via
    ``@dam.fallback`` at import time — no explicit registration step needed.
    """
    from dam.boundary.callbacks import register_all as reg_callbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_guards()


def build_runner(
    stack: str,
    *,
    ros2_node: Any = None,
) -> BaseRunner:
    """Build a :class:`Runner` from a Stackfile path.

    Built-in callbacks/fallbacks/guards are registered first.  The runner is
    returned **built but not connected** — call ``connect()`` / ``verify()``
    / ``start()`` yourself, or use :func:`run` for the managed loop.
    """
    _register_builtins()
    from dam.runtime.factory import RuntimeFactory

    return RuntimeFactory.build_from_stackfile(stack, ros2_node=ros2_node)


def run(
    stack: str,
    *,
    task: str = "default",
    cycles: int = 100,
    ros2_node: Any = None,
) -> RunSummary:
    """Build a runtime from *stack* and run a headless control loop.

    Performs the full managed lifecycle — build → connect → verify → start →
    wait for a terminal state → shutdown — and returns a :class:`RunSummary`.
    ``cycles=-1`` runs unbounded (until stopped/faulted). Build/connect
    failures propagate as exceptions; ``KeyboardInterrupt`` stops the runner
    and shuts down before re-raising.
    """
    import time

    from dam.runner.base import RunnerStatus

    runner = build_runner(stack, ros2_node=ros2_node)
    runner.connect()
    runner.verify()
    runner.start(task=task, n_cycles=cycles)

    terminal = (RunnerStatus.STOPPED, RunnerStatus.IDLE, RunnerStatus.EMERGENCY)
    try:
        while runner.status not in terminal:
            time.sleep(0.05)
    except KeyboardInterrupt:
        runner.stop()
        raise
    finally:
        status = runner.status
        cycles_done = int(getattr(runner, "cycle_count", 0) or 0)
        runner.shutdown()

    return RunSummary(
        status=status.name,
        cycles=cycles_done,
        emergency=status == RunnerStatus.EMERGENCY,
    )


# ---------------------------------------------------------------------------
# Guardrail — lightweight action validation without a hardware loop
# ---------------------------------------------------------------------------


def _resolve_action_layout(config: Any) -> list[dict[str, Any]]:
    hardware = getattr(config, "hardware", None)
    if hardware and hardware.action_layout:
        return [dict(item) for item in hardware.action_layout]
    preset_name = hardware.preset if hardware else None
    if not preset_name:
        return []
    try:
        from dam.preset.registry import get_preset

        return [dict(item) for item in get_preset(preset_name).action_layout]
    except Exception:  # noqa: BLE001
        return []


# Pool keys the runtime owns — a user observation group must never shadow them.
# A callback may still *declare* one (e.g. ``dt``) to read the runtime value;
# the collision check only rejects them as input-dict keys.
_RESERVED_POOL_KEYS = frozenset(
    {
        "obs",
        "action",
        "dt",
        "solvers",
        "joint_names",
        "action_layout",
        "cycle_id",
        "trace_id",
        "timestamp",
        "now",
        "ee_pos",
        "ee_rot",
        "ee_pose",
        "J_linear",
        "J_angular",
        "jacobian_joint_indices",
        "runtime_pool",
        "prev_validated_positions",
        "prev_validated_velocities",
        "active_task",
        "active_boundaries",
        "active_containers",
        "active_map",
        "node_start_times",
        "dynamics",
        "boundary_name",
    }
)


class Guardrail:
    """Filter a policy's command through a safety stackfile — dict in, command out.

    One call per control cycle. You pass a dict: the reserved ``"action"`` key is
    the command to validate, every other key is an observation group. A callback
    receives any group by declaring a parameter of the same name::

        @dam.callback("forward_only")
        def forward_only(*, base_pose, action, dt=0.1):
            ...

        rail = dam.Guardrail("jetbot.yaml", safe_action=[0.0, 0.0])
        safe_cmd = rail({"base_pose": [x, y, yaw], "action": [v, omega]})

    Standard keys are also folded into the ``obs`` object for builtin guards:
    ``joints`` / ``<joint>.pos`` → joint positions, ``images`` / camera frames,
    ``current`` / ``temperature`` / ``voltage`` and any other array group →
    ``obs.channels``. The return value mirrors the ``action`` you passed
    (list/ndarray/tensor, or a ``{key: value}`` dict).

    On **reject** the command is replaced by ``safe_action``:
      * an explicit vector (e.g. ``[0, 0]`` to stop a mobile base),
      * ``"hold"`` (default) — re-issue the current joint positions, the safe
        choice for a position-controlled arm,
      * ``"zero"`` — a zero command.

    Not thread-safe — one instance per control loop.
    """

    def __init__(
        self,
        stackfile: str,
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
        solvers: Mapping[str, Any] | None = None,
        safe_action: Any = "hold",
        quiet: bool = False,
    ) -> None:
        _register_builtins()

        from dam.config.loader import StackfileLoader
        from dam.preset.registry import get_preset
        from dam.runtime.guard_runtime import GuardRuntime

        config = StackfileLoader.load(stackfile)

        # joint_names from preset; degrees_mode from the motor interface
        # (source.degrees_mode / hardware.degrees_mode) — never the preset.
        if joint_names is None or degrees_mode is None:
            hardware = config.hardware
            if hardware and joint_names is None and hardware.joint_names:
                joint_names = list(hardware.joint_names)
            if hardware and degrees_mode is None:
                degrees_mode = hardware.motor_degrees_mode()
            preset_name = hardware.preset if hardware else None
            if preset_name and joint_names is None:
                joint_names = get_preset(preset_name).joint_names
        if joint_names is None:
            raise ValueError(
                "Cannot resolve joint_names: stackfile has no hardware.preset "
                "and joint_names was not provided explicitly."
            )
        if degrees_mode is None:
            degrees_mode = True

        self._joint_names: list[str] = joint_names
        self._degrees_mode: bool = degrees_mode
        self._action_layout: list[dict[str, Any]] = _resolve_action_layout(config)
        self._solvers: dict[str, Any] = dict(solvers or {})
        self._n_joints: int = len(joint_names)

        # Action space derived from action_layout (or the joints when no layout
        # is given). "command space" = the action is not the joint vector →
        # never deg<->rad scaled, reject falls back to a stop command.
        self._action_keys: list[str] | None = None
        self._n_actions: int = self._n_joints
        self._command_space: bool = False
        self._refresh_action_spec()

        # Conversion scales for the joint space (vectorised, bound once).
        scale_in = _DEG2RAD if degrees_mode else 1.0
        scale_out = _RAD2DEG if degrees_mode else 1.0
        self._scale_in = np.full(self._n_joints, scale_in, dtype=np.float64)
        self._scale_out = np.full(self._n_joints, scale_out, dtype=np.float64)

        # Reject fallback.
        self._safe_action_mode, self._safe_action_value = self._resolve_safe_action(safe_action)

        # Build runtime (no sources / policy / sink needed).
        self._runtime = GuardRuntime.from_stackfile(stackfile)
        self._solvers = {**self._runtime._solvers, **self._solvers}
        self._runtime._solvers.update(self._solvers)

        # Start the first task to activate guards and build the stage DAG.
        if task is None:
            task = next(iter(config.tasks))
        self._task = task
        self._runtime.start_task(task)

        # Self-describing contract: which observation keys the active callbacks
        # require (declared parameters without defaults, minus reserved pool
        # keys and per-node stackfile params).
        self._required_obs = self._compute_contract(config, task)

        # If stackfile has loopback:, start MCAP recording.
        self._runtime.start_recording()

        # State for velocity estimation between calls.
        self._prev_positions: np.ndarray | None = None
        self._prev_time: float | None = None
        self._last_results: list[GuardResult] = []
        self._last_ee_pose: np.ndarray | None = None

        if not quiet:
            print(self.describe(stackfile))

    # -- public interface ---------------------------------------------------

    def __call__(self, inputs: dict[str, Any]) -> Any:
        """Validate ``inputs["action"]`` against the observation groups in
        ``inputs``; return the safe command in the same form it arrived."""
        if not isinstance(inputs, dict):
            raise TypeError(
                "Guardrail expects a dict with an 'action' key plus observation "
                f"groups, got {type(inputs).__name__}."
            )
        if "action" not in inputs:
            raise KeyError("Guardrail input is missing the required 'action' key.")

        # Fail fast on a missing required observation group or a reserved-key
        # collision — silent fallbacks hide safety-relevant mistakes.
        provided = set(inputs) - {"action"}
        missing = self._required_obs - provided
        if missing:
            raise KeyError(
                f"Guardrail missing observation group(s) {sorted(missing)}; "
                f"received {sorted(provided)}. Required: {sorted(self._required_obs)}."
            )
        clashes = provided & _RESERVED_POOL_KEYS
        if clashes:
            raise KeyError(
                f"Observation key(s) {sorted(clashes)} collide with reserved runtime "
                "keys — rename them."
            )

        raw_action = inputs["action"]
        action_is_dict = isinstance(raw_action, dict)
        action_is_tensor = not action_is_dict and hasattr(raw_action, "detach")
        tensor_device = raw_action.device if action_is_tensor else None
        tensor_dtype = raw_action.dtype if action_is_tensor else None
        # lerobot dicts key the command as ``<joint>.pos``; bare ``<key>`` else.
        pos_style = action_is_dict and any(str(k).endswith(".pos") for k in raw_action)

        now = time.monotonic()
        if self._prev_time is not None:
            actual_dt = max(now - self._prev_time, 1e-6)
            self._runtime._hot_reload.config_pool["dt"] = actual_dt

        mappable = self._action_mappable(raw_action, action_is_dict, pos_style)
        obs = self._build_observation(inputs, now)
        action = self._build_action(raw_action, action_is_dict, pos_style, mappable)
        trace_id = str(uuid.uuid4())

        validated, results = self._runtime.validate(obs, action, trace_id, now=now)
        self._last_results = results
        self._prev_positions = obs.joint_positions.copy()
        self._prev_time = now

        if not mappable:
            # Opaque action (partial EE-pose dict): echo it back as-is.
            return dict(raw_action)
        if validated is not None:
            command = np.asarray(validated.target_joint_positions, dtype=np.float64).flatten()
        else:
            command = self._reject_command(obs)

        out = self._format_command(command, action_is_dict, pos_style)
        if action_is_tensor:
            import torch as _torch

            return _torch.as_tensor(out, dtype=tensor_dtype, device=tensor_device)
        return out

    def describe(self, stackfile: str = "") -> str:
        """Human-readable contract: which obs keys and action this guard expects."""
        name = Path(stackfile).name if stackfile else "<stackfile>"
        required = ", ".join(sorted(self._required_obs)) or "—"
        action = ", ".join(self._action_keys) if self._action_keys else f"{self._n_actions}-vector"
        space = "command space" if self._command_space else "joint positions"
        if self._safe_action_mode == "hold":
            reject = "hold current position"
        else:
            reject = f"{np.array2string(self._safe_action_value, precision=3)}"
        return "\n".join(
            [
                f"[Guardrail] {name} · task={self._task}",
                f"  requires obs: {required}",
                f"  action:       [{action}]  ({space})",
                f"  on reject:    {reject}",
            ]
        )

    def set_ee_pose(self, ee_pose: Any) -> None:
        """Set the end-effector pose for the next cycle so EE-space guards
        (workspace_bounds, keep_out_zone, ee_velocity_limit) can run.

        ``ee_pose`` is ``[x, y, z, qx, qy, qz, qw]``; pass None to clear. This
        is also accepted as an ``ee_pose`` key in the input dict.
        """
        if ee_pose is None:
            self._last_ee_pose = None
        elif hasattr(ee_pose, "detach"):
            self._last_ee_pose = ee_pose.detach().cpu().numpy().flatten()[:7]
        else:
            self._last_ee_pose = np.asarray(ee_pose, dtype=np.float64).flatten()[:7]

    @property
    def required_obs_keys(self) -> set[str]:
        """Observation group keys every input dict must contain."""
        return set(self._required_obs)

    @property
    def last_results(self) -> list[GuardResult]:
        """Guard results from the most recent :meth:`__call__`."""
        return self._last_results

    @property
    def action_layout(self) -> list[dict[str, Any]]:
        """Policy action layout used by this guard."""
        return [dict(item) for item in self._action_layout]

    @property
    def solvers(self) -> dict[str, Any]:
        """Solver objects provided to this guard, keyed by solver name."""
        return dict(self._solvers)

    @property
    def runtime(self) -> Any:
        """The underlying :class:`GuardRuntime` (advanced use)."""
        return self._runtime

    def close(self) -> None:
        """Stop MCAP recording if active."""
        self._runtime.stop_recording()

    def __del__(self) -> None:
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.stop_recording()

    # -- internals ----------------------------------------------------------

    def _resolve_safe_action(self, safe_action: Any) -> tuple[str, np.ndarray]:
        if isinstance(safe_action, str):
            if safe_action == "hold":
                # Holding the last command is unsafe for a velocity base, so a
                # command-space guard stops instead.
                if self._command_space:
                    return "fixed", np.zeros(self._n_actions, dtype=np.float64)
                return "hold", np.zeros(self._n_actions, dtype=np.float64)
            if safe_action == "zero":
                return "fixed", np.zeros(self._n_actions, dtype=np.float64)
            raise ValueError(
                f"safe_action must be a vector, 'hold', or 'zero'; got {safe_action!r}"
            )
        return "fixed", np.asarray(safe_action, dtype=np.float64).flatten()

    def _compute_contract(self, config: Any, task: str) -> set[str]:
        """Observation groups the active callbacks require: their declared
        parameters without defaults, minus reserved pool keys and the per-node
        stackfile params (which the runtime already supplies)."""
        import inspect

        from dam.registry.callback import get_global_registry

        registry = get_global_registry()
        required: set[str] = set()
        task_cfg = config.tasks.get(task)
        boundary_names = list(task_cfg.boundaries) if task_cfg else list(config.boundaries)
        for bname in boundary_names:
            container = config.boundaries.get(bname)
            if container is None:
                continue
            for node in container.nodes:
                cb_name = node.callback
                if not cb_name:
                    continue
                try:
                    fn = registry.get(cb_name)
                except KeyError:
                    continue
                node_params = set(node.params or {})
                for p in inspect.signature(fn).parameters.values():
                    if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                        continue
                    if p.name in _RESERVED_POOL_KEYS or p.name in node_params:
                        continue
                    if p.default is inspect.Parameter.empty:
                        required.add(p.name)
        return required

    def _build_observation(self, inputs: dict[str, Any], now: float) -> Any:
        from dam.types.observation import Observation

        images: dict[str, Any] = {}
        channels: dict[str, Any] = {}
        joint_pos: np.ndarray | None = None
        ee_pose = self._last_ee_pose

        for key, value in inputs.items():
            if key == "action":
                continue
            if key == "images" and isinstance(value, dict):
                images.update(value)
            elif key.startswith("observation.images.") or key.startswith("images."):
                images[key.rsplit(".", 1)[-1]] = value
            elif key == "joints":
                joint_pos = np.asarray(value, dtype=np.float64).flatten()
            elif key == "ee_pose":
                ee_pose = np.asarray(value, dtype=np.float64).flatten()[:7]
            elif not key.endswith(".pos"):
                channels[key] = np.asarray(value, dtype=np.float64).flatten()

        if joint_pos is None and any(k.endswith(".pos") for k in inputs):
            joint_pos = np.fromiter(
                (float(inputs.get(f"{n}.pos", 0.0)) for n in self._joint_names),
                dtype=np.float64,
                count=self._n_joints,
            )
        if joint_pos is None:
            joint_pos = np.zeros(0, dtype=np.float64)
        else:
            joint_pos = joint_pos * self._scale_in[: joint_pos.shape[0]]

        velocities = self._estimate_velocity(joint_pos, now) if joint_pos.shape[0] else None
        return Observation(
            timestamp=now,
            joint_positions=joint_pos,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=images or None,
            channels=channels or None,
        )

    def _refresh_action_spec(self) -> None:
        """(Re)derive ``_n_actions`` / ``_action_keys`` / ``_command_space`` from
        the current action_layout. Tests mutate ``_action_layout`` and call this."""
        keys: list[str] = []
        size = 0
        fully_keyed = bool(self._action_layout)
        for segment in self._action_layout:
            seg_keys = segment.get("keys")
            if isinstance(seg_keys, (list, tuple)):
                keys.extend(str(k) for k in seg_keys)
            else:
                fully_keyed = False
            size += _segment_size(segment) or 0
        if not self._action_layout or size == 0:
            self._n_actions = self._n_joints
            self._action_keys = list(self._joint_names)
            self._command_space = False
        else:
            self._n_actions = size
            self._action_keys = keys if fully_keyed else None
            self._command_space = self._action_keys != list(self._joint_names)

    def _action_mappable(self, raw: Any, is_dict: bool, pos_style: bool) -> bool:
        """Can this action be placed into the n_actions vector? Array always;
        dict only when the layout is fully keyed and all keys are present."""
        if not is_dict:
            return True
        if self._action_keys is None:
            return False
        suffix = ".pos" if pos_style else ""
        return all(f"{k}{suffix}" in raw for k in self._action_keys)

    def _build_action(self, raw: Any, is_dict: bool, pos_style: bool, mappable: bool) -> Any:
        from dam.types.action import ActionProposal

        if not mappable:
            # Opaque action (e.g. a partial EE-pose dict): guards inspect it via
            # metadata; the command is echoed back unchanged by _format_command.
            target = np.zeros(self._n_actions, dtype=np.float64)
        elif is_dict:
            suffix = ".pos" if pos_style else ""
            target = np.fromiter(
                (float(raw[f"{k}{suffix}"]) for k in self._action_keys or []),
                dtype=np.float64,
                count=self._n_actions,
            )
        else:
            target = np.asarray(raw, dtype=np.float64).flatten()[: self._n_actions]
        if mappable and not self._command_space:
            target = target * self._scale_in[: target.shape[0]]

        metadata = {
            "raw_action": dict(raw) if is_dict else np.asarray(raw).copy(),
            "action_layout": [dict(item) for item in self._action_layout],
            "action_segments": self._split_action(target),
        }
        return ActionProposal(target_joint_positions=target, metadata=metadata)

    def _split_action(self, arr: np.ndarray) -> dict[str, Any]:
        segments: dict[str, Any] = {}
        cursor = 0
        for index, segment in enumerate(self._action_layout):
            name = str(segment.get("name") or f"segment_{index}")
            size = _segment_size(segment)
            if size is None:
                continue
            segments[name] = arr[cursor : cursor + size].copy()
            cursor += size
        return segments

    def _reject_command(self, obs: Any) -> np.ndarray:
        if self._safe_action_mode == "hold":
            return np.asarray(obs.joint_positions, dtype=np.float64).flatten()
        return self._safe_action_value.copy()

    def _format_command(self, command: np.ndarray, as_dict: bool, pos_style: bool) -> Any:
        if not self._command_space:
            command = command[: self._n_joints] * self._scale_out[: command.shape[0]]
        if as_dict:
            keys = self._action_keys or [str(i) for i in range(command.shape[0])]
            suffix = ".pos" if pos_style else ""
            return {f"{keys[i]}{suffix}": float(command[i]) for i in range(len(keys))}
        return command

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if (
            self._prev_positions is not None
            and self._prev_time is not None
            and self._prev_positions.shape == positions.shape
        ):
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt  # type: ignore[no-any-return]
        return np.zeros_like(positions)


def _segment_size(segment: dict[str, Any]) -> int | None:
    """Resolve one action_layout segment's width: ``keys`` length, an explicit
    ``size``, or a typed default (``ee_pose`` → 7, ``scalar`` → 1)."""
    seg_keys = segment.get("keys")
    if isinstance(seg_keys, (list, tuple)):
        return len(seg_keys)
    declared = segment.get("size") or segment.get("dim") or segment.get("dimensions")
    if declared is not None:
        return int(declared)
    return {"ee_pose": 7, "scalar": 1}.get(str(segment.get("type", "")).lower())


def guardrail(
    inputs: dict[str, Any],
    stackfile: str = "safety.yaml",
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
    solvers: Mapping[str, Any] | None = None,
    safe_action: Any = "hold",
) -> Any:
    """Validate one input dict against a safety stackfile.

    Convenience one-liner — builds a :class:`Guardrail` internally. For repeated
    calls use ``Guardrail`` directly to amortise setup.
    """
    rail = Guardrail(
        stackfile,
        task=task,
        joint_names=joint_names,
        degrees_mode=degrees_mode,
        solvers=solvers,
        safe_action=safe_action,
        quiet=True,
    )
    return rail(inputs)
