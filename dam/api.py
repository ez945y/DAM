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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

        self._joint_names: list[str] = joint_names
        self._degrees_mode: bool = degrees_mode
        self._n_joints: int = len(joint_names)

        # Conversion scales (vectorised, bound once).
        scale_in = _DEG2RAD if degrees_mode else 1.0
        scale_out = _RAD2DEG if degrees_mode else 1.0
        self._scale_in = np.full(self._n_joints, scale_in, dtype=np.float64)
        self._scale_out = np.full(self._n_joints, scale_out, dtype=np.float64)

        # Build runtime (no sources / policy / sink needed).
        self._runtime = GuardRuntime.from_stackfile(stackfile)

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

    # -- public interface ---------------------------------------------------

    def __call__(
        self,
        action: np.ndarray | dict[str, Any],
        obs: np.ndarray | dict[str, Any],
    ) -> np.ndarray | dict[str, Any]:
        """Validate *action* given *obs*; return the safe version.

        Accepts and returns the same type — ``dict[str, Any]`` (lerobot
        format with ``{joint}.pos`` keys) or ``np.ndarray``.
        """
        input_is_dict = isinstance(action, dict)
        now = time.monotonic()

        # Use actual dt between calls so velocity limits stay accurate
        # when the caller's loop runs slower than control_frequency_hz.
        if self._prev_time is not None:
            actual_dt = max(now - self._prev_time, 1e-6)
            self._runtime._hot_reload.config_pool["dt"] = actual_dt

        dam_obs = self._to_observation(obs)
        dam_action = self._to_action_proposal(action)
        trace_id = str(uuid.uuid4())

        validated, results = self._runtime.validate(dam_obs, dam_action, trace_id, now=now)
        self._last_results = results

        # Update velocity-estimation state.
        self._prev_positions = dam_obs.joint_positions.copy()
        self._prev_time = now

        if validated is None:
            # Rejected → hold current position (safest IL fallback).
            return self._to_output(dam_obs.joint_positions, input_is_dict)

        return self._to_output(validated.target_joint_positions, input_is_dict)

    @property
    def last_results(self) -> list[GuardResult]:
        """Guard results from the most recent :meth:`__call__`."""
        return self._last_results

    @property
    def runtime(self) -> Any:
        """The underlying :class:`GuardRuntime` (advanced use)."""
        return self._runtime

    def close(self) -> None:
        """Stop MCAP recording if active."""
        self._runtime.stop_recording()

    def __del__(self) -> None:
        self._runtime.stop_recording()

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

    def _to_output(self, positions_rad: np.ndarray, as_dict: bool) -> np.ndarray | dict[str, Any]:
        scaled = positions_rad[: self._n_joints] * self._scale_out
        if as_dict:
            return {f"{self._joint_names[i]}.pos": float(scaled[i]) for i in range(self._n_joints)}
        return scaled

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if self._prev_positions is not None and self._prev_time is not None:
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt  # type: ignore[no-any-return]
        return np.zeros_like(positions)


def safe(
    action: np.ndarray | dict[str, Any],
    obs: np.ndarray | dict[str, Any],
    stackfile: str = "safety.yaml",
    *,
    task: str | None = None,
    joint_names: list[str] | None = None,
    degrees_mode: bool | None = None,
) -> np.ndarray | dict[str, Any]:
    """Validate a single action against a safety stackfile.

    Convenience one-liner — creates a :class:`SafetyGuard` internally.
    For repeated calls use ``SafetyGuard`` directly to amortise setup.
    """
    guard = SafetyGuard(
        stackfile,
        task=task,
        joint_names=joint_names,
        degrees_mode=degrees_mode,
    )
    return guard(action, obs)
