"""Public programmatic API for embedding DAM as a library.

``dam.build_runner`` / ``dam.run`` are thin, stable wrappers over
``RuntimeFactory`` so callers don't have to know the internal factory /
registry wiring.  The ``dam`` CLI's ``run`` subcommand is a thin shell over
``dam.run`` — single source of truth.

``dam.SafetyGuard`` / ``dam.safe`` provide a lightweight action-validation
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
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from dam.preset.registry import RobotPreset
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


def register_preset(
    name: str,
    *,
    joint_names: list[str],
    degrees_mode: bool = True,
    assets: dict[str, str] | None = None,
    solvers: dict[str, Any] | None = None,
    chains: dict[str, Any] | None = None,
) -> RobotPreset:
    """Register or update a robot preset from library code.

    The preset is written to DAM's user registry
    (``${DAM_DATA_ROOT}/presets.yaml``), so it works in pip-installed
    environments where the bundled ``assets/presets.yaml`` is read-only.
    """
    from dam.preset.registry import upsert_preset

    return upsert_preset(
        name,
        joint_names=joint_names,
        degrees_mode=degrees_mode,
        assets=assets,
        solvers=solvers,
        chains=chains,
    )


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


def register_solver_factory(
    solver_type: str,
    factory: Callable[[Mapping[str, Any]], Any],
    *,
    capabilities: tuple[str, ...] | list[str],
    replace: bool = False,
) -> Callable[[Mapping[str, Any]], Any]:
    """Register a config-driven solver factory.

    Stackfiles can instantiate it with:

    .. code-block:: yaml

        solvers:
          arm:
            type: my_solver_type
            params: {...}
    """
    from dam.solver.registry import get_global_solver_registry

    return get_global_solver_registry().register_factory(
        solver_type,
        factory,
        capabilities=capabilities,
        replace=replace,
    )


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


def build_runner(stack: str, *, ros2_node: Any = None) -> BaseRunner:
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
# SafetyGuard — lightweight action validation without a hardware loop
# ---------------------------------------------------------------------------


class SafetyGuard:
    """Stateful safety validator — no hardware loop needed.

    Wraps a :class:`GuardRuntime` built from a stackfile.  Call the instance
    to validate one action per cycle; the return value has the **same type
    and shape** as the input (``dict`` or ``ndarray``).

    .. code-block:: python

        guard = dam.SafetyGuard("safety.yaml")
        safe_action = guard(action, obs)   # dict→dict or ndarray→ndarray

    If the action is **rejected** by the guards, the current observation's
    joint positions are returned (hold-position fallback) so the recording
    loop never breaks.

    Not thread-safe — one instance per control loop.
    """

    def __init__(
        self,
        stackfile: str,
        *,
        task: str | None = None,
        joint_names: list[str] | None = None,
        degrees_mode: bool | None = None,
        input_space: str | None = None,
        solvers: Mapping[str, Any] | None = None,
    ) -> None:
        _register_builtins()

        from dam.config.loader import StackfileLoader
        from dam.preset.registry import get_preset
        from dam.runtime.guard_runtime import GuardRuntime

        config = StackfileLoader.load(stackfile)

        # Resolve joint_names / degrees_mode from preset when not explicit.
        if joint_names is None or degrees_mode is None:
            preset_name = config.hardware.preset if config.hardware else None
            if preset_name:
                preset = get_preset(preset_name)
                if joint_names is None:
                    joint_names = preset.joint_names
                if degrees_mode is None:
                    degrees_mode = preset.degrees_mode
        if joint_names is None:
            raise ValueError(
                "Cannot resolve joint_names: stackfile has no hardware.preset "
                "and joint_names was not provided explicitly."
            )
        if degrees_mode is None:
            degrees_mode = True
        resolved_input_space = (
            input_space
            if input_space is not None
            else (config.hardware.input_space if config.hardware else "joint")
        )
        resolved_input_space = str(resolved_input_space).lower()
        valid_input_spaces = {"joint", "ee", "base", "twist", "ackermann", "pose2d"}
        if resolved_input_space not in valid_input_spaces:
            raise ValueError(f"input_space must be one of {sorted(valid_input_spaces)}")

        self._joint_names: list[str] = joint_names
        self._degrees_mode: bool = degrees_mode
        self._input_space: str = resolved_input_space
        self._solvers: dict[str, Any] = dict(solvers or {})
        self._n_joints: int = len(joint_names)

        # Conversion scales (vectorised, bound once).
        scale_in = _DEG2RAD if degrees_mode else 1.0
        scale_out = _RAD2DEG if degrees_mode else 1.0
        self._scale_in = np.full(self._n_joints, scale_in, dtype=np.float64)
        self._scale_out = np.full(self._n_joints, scale_out, dtype=np.float64)

        # Build runtime (no sources / policy / sink needed).
        self._runtime = GuardRuntime.from_stackfile(stackfile)
        self._solvers = {**self._runtime._solvers, **self._solvers}
        self._runtime._solvers.update(self._solvers)

        # Start the first task to activate guards and build the stage DAG.
        if task is None:
            task = next(iter(config.tasks))
        self._runtime.start_task(task)

        # If stackfile has loopback:, start MCAP recording.
        self._runtime.start_recording()

        # State for velocity estimation between calls.
        self._prev_positions: np.ndarray | None = None
        self._prev_time: float | None = None
        self._last_results: list[GuardResult] = []
        self._last_ee_pose: np.ndarray | None = None

    # -- public interface ---------------------------------------------------

    def __call__(
        self,
        action: Any,
        obs: Any,
    ) -> Any:
        """Validate *action* given *obs*; return the safe version.

        Accepts and returns the same type — ``dict[str, Any]`` (lerobot
        format with ``{joint}.pos`` keys), ``np.ndarray``, or
        ``torch.Tensor`` (returned on same device/dtype).
        """
        input_is_dict = isinstance(action, dict)
        input_is_tensor = not input_is_dict and hasattr(action, "detach")
        _tensor_device: Any = None
        _tensor_dtype: Any = None

        if input_is_tensor:
            _tensor_device = action.device
            _tensor_dtype = action.dtype
            action = action.detach().cpu().numpy()
        if not isinstance(obs, dict) and hasattr(obs, "detach"):
            obs = obs.detach().cpu().numpy()

        now = time.monotonic()

        # Use actual dt between calls so velocity limits stay accurate
        # when the caller's loop runs slower than control_frequency_hz.
        if self._prev_time is not None:
            actual_dt = max(now - self._prev_time, 1e-6)
            self._runtime._hot_reload.config_pool["dt"] = actual_dt

        dam_obs = self._to_observation(obs)
        if self._input_space == "ee":
            dam_action = self._to_ee_action_proposal(action, dam_obs.joint_positions)
        else:
            dam_action = self._to_action_proposal(action)
        trace_id = str(uuid.uuid4())

        validated, results = self._runtime.validate(dam_obs, dam_action, trace_id, now=now)
        self._last_results = results

        # Update velocity-estimation state.
        self._prev_positions = dam_obs.joint_positions.copy()
        self._prev_time = now

        if self._input_space == "ee":
            safe_positions = (
                dam_obs.joint_positions if validated is None else validated.target_joint_positions
            )
            out = self._to_ee_output(safe_positions, input_is_dict)
        elif validated is None:
            out = self._to_output(dam_obs.joint_positions, input_is_dict)
        else:
            out = self._to_output(validated.target_joint_positions, input_is_dict)

        if input_is_tensor:
            import torch as _torch

            return _torch.as_tensor(out, dtype=_tensor_dtype, device=_tensor_device)
        return out

    def set_ee_pose(self, ee_pose: Any) -> None:
        """Set end-effector pose for the next guard cycle.

        Call this before ``__call__`` when EE pose is available (e.g. from
        Isaac Sim, FK, or a motion capture system). The pose is included in
        the Observation so EE-space guards (workspace_bounds, keep_out_zone,
        ee_velocity_limit) can function.

        Args:
            ee_pose: [x, y, z, qx, qy, qz, qw] as ndarray, list, or torch.Tensor.
                     Pass None to clear.
        """
        if ee_pose is None:
            self._last_ee_pose = None
        elif hasattr(ee_pose, "detach"):
            self._last_ee_pose = ee_pose.detach().cpu().numpy().flatten()[:7]
        else:
            self._last_ee_pose = np.asarray(ee_pose, dtype=np.float64).flatten()[:7]

    @property
    def last_results(self) -> list[GuardResult]:
        """Guard results from the most recent :meth:`__call__`."""
        return self._last_results

    @property
    def input_space(self) -> str:
        """Action space accepted by this guard."""
        return self._input_space

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

    def _select_solver(self, capability: str) -> Any | None:
        capability = capability.lower()
        for name, solver in self._solvers.items():
            if name.lower() == capability:
                return solver
            caps = getattr(solver, "_dam_solver_capabilities", ())
            if capability in caps:
                return solver
        return None

    # -- conversion helpers -------------------------------------------------

    def _to_observation(self, raw: np.ndarray | dict[str, Any]) -> Any:
        from dam.types.observation import Observation

        now = time.monotonic()

        if isinstance(raw, dict):
            positions = np.fromiter(
                (float(raw.get(f"{n}.pos", 0.0)) for n in self._joint_names),
                dtype=np.float64,
                count=self._n_joints,
            )
            positions = positions * self._scale_in
        else:
            positions = np.asarray(raw, dtype=np.float64).flatten()[: self._n_joints]
            positions = positions * self._scale_in

        velocities = self._estimate_velocity(positions, now)

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=self._last_ee_pose,
        )

    def _to_action_proposal(self, raw: np.ndarray | dict[str, Any]) -> Any:
        from dam.types.action import ActionProposal

        if isinstance(raw, dict):
            target = np.fromiter(
                (float(raw.get(f"{n}.pos", 0.0)) for n in self._joint_names),
                dtype=np.float64,
                count=self._n_joints,
            )
            target = target * self._scale_in
        else:
            target = np.asarray(raw, dtype=np.float64).flatten()[: self._n_joints]
            target = target * self._scale_in

        return ActionProposal(target_joint_positions=target)

    def _to_ee_action_proposal(
        self, raw: np.ndarray | dict[str, Any], current_joints: np.ndarray
    ) -> Any:
        from dam.types.action import ActionProposal

        if isinstance(raw, dict):
            raise ValueError("SafetyGuard input_space='ee' expects an EE pose array, not a dict")
        kinematics = self._select_solver("kinematics")
        if kinematics is None:
            raise ValueError(
                "SafetyGuard input_space='ee' requires a configured kinematics solver. "
                "Use input_space='joint' for joint targets, or pass solvers={'kinematics': solver}."
            )
        target_ee_pose = np.asarray(raw, dtype=np.float64).flatten()
        if target_ee_pose.shape[0] != 7:
            raise ValueError(
                f"EE action must contain exactly 7 values [x,y,z,qx,qy,qz,qw], got {target_ee_pose.shape[0]}"
            )
        try:
            ik_result = kinematics.inverse_kinematics(target_ee_pose, current_joints)
        except Exception:
            logger.warning(
                "Kinematics solver IK failed; holding current joint positions", exc_info=True
            )
            return ActionProposal(
                target_joint_positions=current_joints[: self._n_joints].copy(),
                target_ee_pose=target_ee_pose,
            )
        target_joints = np.asarray(ik_result, dtype=np.float64).flatten()
        if target_joints.shape[0] != self._n_joints:
            raise ValueError(
                f"Kinematics solver IK returned {target_joints.shape[0]} joints, expected {self._n_joints}"
            )
        if not np.all(np.isfinite(target_joints)):
            logger.warning(
                "Kinematics solver IK returned non-finite values; holding current joint positions"
            )
            return ActionProposal(
                target_joint_positions=current_joints[: self._n_joints].copy(),
                target_ee_pose=target_ee_pose,
            )
        return ActionProposal(
            target_joint_positions=target_joints,
            target_ee_pose=target_ee_pose,
        )

    def _to_output(self, positions_rad: np.ndarray, as_dict: bool) -> np.ndarray | dict[str, Any]:
        scaled = positions_rad[: self._n_joints] * self._scale_out
        if as_dict:
            return {f"{self._joint_names[i]}.pos": float(scaled[i]) for i in range(self._n_joints)}
        return scaled

    def _to_ee_output(
        self, positions_rad: np.ndarray, as_dict: bool
    ) -> np.ndarray | dict[str, Any]:
        if as_dict:
            raise ValueError("SafetyGuard input_space='ee' does not support dict output")
        kinematics = self._select_solver("kinematics")
        if kinematics is None:
            raise ValueError("SafetyGuard input_space='ee' requires a configured kinematics solver")
        try:
            fk_result = kinematics.forward_kinematics(positions_rad[: self._n_joints])
        except Exception:
            logger.warning(
                "Kinematics solver FK failed; returning last known EE pose", exc_info=True
            )
            return self._last_ee_pose.copy() if self._last_ee_pose is not None else np.zeros(7)
        ee_pose = np.asarray(fk_result, dtype=np.float64).flatten()
        if ee_pose.shape[0] != 7:
            raise ValueError(
                f"Kinematics solver FK must return exactly 7 values [x,y,z,qx,qy,qz,qw], got {ee_pose.shape[0]}"
            )
        if not np.all(np.isfinite(ee_pose)):
            logger.warning(
                "Kinematics solver FK returned non-finite values; returning last known EE pose"
            )
            return self._last_ee_pose.copy() if self._last_ee_pose is not None else np.zeros(7)
        self._last_ee_pose = ee_pose.copy()
        return ee_pose

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if self._prev_positions is not None and self._prev_time is not None:
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt  # type: ignore[no-any-return]
        return np.zeros_like(positions)


def safe(
    action: Any,
    obs: Any,
    stackfile: str = "safety.yaml",
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
    input_space: str | None = None,
    solvers: Mapping[str, Any] | None = None,
) -> Any:
    """Validate a single action against a safety stackfile.

    Convenience one-liner — creates a :class:`SafetyGuard` internally.
    For repeated calls use ``SafetyGuard`` directly to amortise setup.
    Accepts ``np.ndarray``, ``dict``, or ``torch.Tensor`` (same device/dtype preserved).
    """
    guard = SafetyGuard(
        stackfile,
        task=task,
        joint_names=joint_names,
        degrees_mode=degrees_mode,
        input_space=input_space,
        solvers=solvers,
    )
    return guard(action, obs)
