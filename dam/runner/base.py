"""BaseRunner — Abstract interface for all DAM execution strategies.

A Runner is responsible for the complete lifecycle of a single hardware/task
configuration: from physical connection and health verification to the
actual execution of the control loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from dam.runtime.guard_runtime import GuardRuntime


class BaseRunner(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Establish physical connection to hardware/simulator."""
        pass

    @abstractmethod
    def verify(self) -> None:
        """Run aggregate preflight health checks. Raises RuntimeError on failure."""
        pass

    @abstractmethod
    def step(self) -> Any:
        """Execute a single control cycle (Obs -> Policy -> Guard -> Sink)."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Gracefully disconnect and release all hardware resources."""
        pass

    @property
    @abstractmethod
    def runtime(self) -> GuardRuntime:
        """Access the underlying GuardRuntime for introspection."""
        pass


class SimulationRunner(BaseRunner):
    """Execution strategy for simulated environments (Datasets, Mock robots)."""

    def __init__(self, runtime: GuardRuntime, control_frequency_hz: float = 10.0) -> None:
        self._runtime = runtime
        self._hz = control_frequency_hz

    @property
    def runtime(self) -> GuardRuntime:
        return self._runtime

    def connect(self) -> None:
        # Connect all sources
        for src in self._runtime._sources.values():
            if hasattr(src, "connect"):
                src.connect()

    def verify(self) -> None:
        # Verify all sources
        for src in self._runtime._sources.values():
            if hasattr(src, "verify"):
                src.verify()

    def step(self) -> Any:
        return self._runtime.step()

    def shutdown(self) -> None:
        import contextlib

        with contextlib.suppress(BaseException):
            self._runtime.stop_task()
        # Shutdown all sources
        for src in self._runtime._sources.values():
            if hasattr(src, "disconnect"):
                src.disconnect()
            elif hasattr(src, "shutdown"):
                src.shutdown()
