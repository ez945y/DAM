"""Public programmatic API for embedding DAM as a library.

``dam.build_runner`` / ``dam.run`` are thin, stable wrappers over
``RuntimeFactory`` so callers don't have to know the internal factory /
registry wiring.  The ``dam`` CLI's ``run`` subcommand is a thin shell over
``dam.run`` — single source of truth.

Heavy dependencies (factory, adapters, torch, …) are imported lazily inside
the functions so ``import dam`` stays cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.runner.base import BaseRunner


@dataclass(frozen=True)
class RunSummary:
    """Outcome of a :func:`run` invocation."""

    status: str  # RunnerStatus name, e.g. "STOPPED", "EMERGENCY"
    cycles: int
    emergency: bool


def _register_builtins() -> None:
    """Register built-in callbacks, fallbacks, and guards (idempotent)."""
    from dam.boundary.callbacks import register_all as reg_callbacks
    from dam.fallback.builtin import register_all as reg_fallbacks
    from dam.guard.builtin import register_all as reg_guards

    reg_callbacks()
    reg_fallbacks()
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
