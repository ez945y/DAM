"""BaseRunner — Abstract interface for all DAM execution strategies.

A Runner is responsible for the complete lifecycle of a single hardware/task
configuration: from physical connection and health verification to the
actual execution of the control loop.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import Any, cast

from dam.runtime.guard_runtime import GuardRuntime

logger = logging.getLogger(__name__)


class RunnerStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    EMERGENCY = "emergency"


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
    def start(
        self, task: str = "default", n_cycles: int = -1, cycle_budget_ms: float | None = None
    ) -> bool:
        """Start the control loop for a specific task."""
        pass

    @abstractmethod
    def pause(self) -> bool:
        """Pause the control loop after the current cycle."""
        pass

    @abstractmethod
    def resume(self) -> bool:
        """Resume a paused control loop."""
        pass

    @abstractmethod
    def stop(self) -> bool:
        """Stop the control loop."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Gracefully disconnect and release all hardware resources."""
        pass

    @property
    @abstractmethod
    def status(self) -> RunnerStatus:
        """Return current runner state."""
        pass

    @property
    @abstractmethod
    def control_frequency_hz(self) -> float:
        """Return the nominal control frequency in Hz."""
        pass


class RuntimeLoopRunner(BaseRunner):
    """Base implementation for runners that own a GuardRuntime control loop."""

    def __init__(
        self,
        runtime: GuardRuntime,
        control_frequency_hz: float = 30.0,
        frame_hub: Any | None = None,
        auxiliary_sources: dict[str, Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._frame_hub = frame_hub
        self._auxiliary_sources = dict(auxiliary_sources or {})
        self._control_frequency_hz = control_frequency_hz
        self._period_sec = 1.0 / control_frequency_hz
        self._status = RunnerStatus.IDLE
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._run_thread: threading.Thread | None = None
        self._cycle_count = 0
        self._error: str | None = None
        self._active_task: str | None = None
        self._step_fn: Callable[[], Any] = self._runtime.step
        self._on_cycle: Callable[[Any], None] | None = None
        self._on_fault: Callable[[str], None] | None = None
        self._on_finished: Callable[[RunnerStatus], None] | None = None
        self._shutdown_complete = False

    @property
    def runtime(self) -> GuardRuntime:
        return self._runtime

    @property
    def status(self) -> RunnerStatus:
        with self._lock:
            return self._status

    @property
    def control_frequency_hz(self) -> float:
        return self._control_frequency_hz

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    def set_lifecycle_callbacks(
        self,
        *,
        on_cycle: Callable[[Any], None] | None = None,
        on_fault: Callable[[str], None] | None = None,
        on_finished: Callable[[RunnerStatus], None] | None = None,
    ) -> None:
        self._on_cycle = on_cycle
        self._on_fault = on_fault
        self._on_finished = on_finished

    def set_step_wrapper(self, wrapper: Callable[[Callable[[], Any]], Callable[[], Any]]) -> None:
        """Wrap the runner-owned step callable without exposing runtime.step to callers."""
        self._step_fn = wrapper(self._runtime.step)

    def _mark_connected(self) -> None:
        with self._lock:
            self._shutdown_complete = False
        if hasattr(self._runtime, "_shutdown_complete"):
            self._runtime._shutdown_complete = False

    def clear_fault(self) -> None:
        """Allow RuntimeControlService to acknowledge a fault after reconnect/verify."""
        with self._lock:
            if self._status in (RunnerStatus.EMERGENCY, RunnerStatus.STOPPED):
                self._status = RunnerStatus.IDLE
            self._error = None
            self._active_task = None

    def start(
        self, task: str = "default", n_cycles: int = -1, cycle_budget_ms: float | None = None
    ) -> bool:
        with self._lock:
            if self._status not in (RunnerStatus.IDLE, RunnerStatus.STOPPED):
                logger.warning(
                    "%s.start(): cannot start while %s", type(self).__name__, self._status
                )
                return False
            self._status = RunnerStatus.STARTING
            self._error = None
            self._cycle_count = 0
            self._active_task = task
            self._pause_event.set()

        try:
            self._runtime.start_task(task)
            if hasattr(self._runtime, "start_recording"):
                self._runtime.start_recording()
        except Exception as exc:
            with contextlib.suppress(Exception):
                if hasattr(self._runtime, "stop_recording"):
                    self._runtime.stop_recording()
            with self._lock:
                self._status = RunnerStatus.IDLE
                self._active_task = None
                self._error = str(exc)
            raise
        period_s = (cycle_budget_ms / 1000.0) if cycle_budget_ms is not None else self._period_sec
        self._run_thread = threading.Thread(
            target=self._run_loop,
            args=(n_cycles, period_s),
            daemon=False,
            name=f"{type(self).__name__}-loop",
        )
        with self._lock:
            self._status = RunnerStatus.RUNNING
        self._run_thread.start()
        return True

    def pause(self) -> bool:
        with self._lock:
            if self._status != RunnerStatus.RUNNING:
                return False
            self._status = RunnerStatus.PAUSED
        self._pause_event.clear()
        with contextlib.suppress(Exception):
            self._runtime.pause_task()
        return True

    def resume(self) -> bool:
        with self._lock:
            if self._status != RunnerStatus.PAUSED:
                return False
            self._status = RunnerStatus.RUNNING
        with contextlib.suppress(Exception):
            self._runtime.resume_task()
        self._pause_event.set()
        return True

    def stop(self) -> bool:
        with self._lock:
            if self._status not in (
                RunnerStatus.RUNNING,
                RunnerStatus.PAUSED,
                RunnerStatus.STARTING,
            ):
                return False
            self._status = RunnerStatus.STOPPING
        self._pause_event.set()
        with contextlib.suppress(Exception):
            self._runtime.stop()

        run_thread = self._run_thread
        if run_thread is not None and run_thread is not threading.current_thread():
            run_thread.join(timeout=5.0)

        if run_thread is None or not run_thread.is_alive():
            with contextlib.suppress(Exception):
                if run_thread is None:
                    self._runtime.stop_task()
            with self._lock:
                self._status = RunnerStatus.STOPPED
                self._active_task = None
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown_complete:
                return
            self._shutdown_complete = True
        self.stop()
        self._pause_event.set()
        run_thread = self._run_thread
        if run_thread is not None and run_thread is not threading.current_thread():
            run_thread.join(timeout=2.0)
        with contextlib.suppress(BaseException):
            self._runtime.stop_task()
        with contextlib.suppress(BaseException):
            self._runtime.shutdown()
        for src in self._auxiliary_sources.values():
            with contextlib.suppress(BaseException):
                if hasattr(src, "disconnect"):
                    src.disconnect()
                elif hasattr(src, "shutdown"):
                    src.shutdown()
        with self._lock:
            self._status = RunnerStatus.STOPPED
            self._active_task = None

    def force_save_mcap(self) -> None:
        loopback = getattr(self._runtime, "_loopback", None)
        if loopback is not None and hasattr(loopback, "force_rotate"):
            loopback.force_rotate()

    def get_latest_images(self) -> dict[str, bytes]:
        if self._frame_hub is not None and hasattr(self._frame_hub, "latest_jpegs"):
            hub_jpegs = self._frame_hub.latest_jpegs()
            if hub_jpegs:
                return {str(name): bytes(jpeg) for name, jpeg in hub_jpegs.items()}
        return {}

    @property
    def metric_bus(self) -> Any | None:
        return getattr(self._runtime, "_metric_bus", None)

    def metric_snapshot(self) -> dict[str, Any]:
        metric_bus = self.metric_bus
        if metric_bus is not None and hasattr(metric_bus, "snapshot"):
            return cast(dict[str, Any], metric_bus.snapshot())
        return {}

    @property
    def mcap_session_filename(self) -> str | None:
        loopback = getattr(self._runtime, "_loopback", None)
        session_path = getattr(loopback, "_session_path", None)
        return session_path.name if session_path else None

    @property
    def mcap_output_dir(self) -> str | None:
        loopback = getattr(self._runtime, "_loopback", None)
        output_dir = getattr(loopback, "_output_dir", None)
        return str(output_dir) if output_dir else None

    def status_snapshot(self) -> dict[str, Any]:
        """Return runner/runtime metadata needed by controllers without leaking runtime."""
        task_config = getattr(self._runtime, "_task_config", {}) or {}
        available_tasks = list(task_config.keys())
        planned_task = (
            "default"
            if "default" in task_config
            else (available_tasks[0] if available_tasks else None)
        )
        planned_boundaries = list(task_config.get(planned_task, [])) if planned_task else []
        active_task = getattr(self._runtime, "_active_task", None)
        active_boundaries = list(getattr(self._runtime, "_active_container_names", []))
        return {
            "status": self.status,
            "cycle_count": self.cycle_count,
            "error": self.error,
            "active_task": active_task if active_task else planned_task,
            "active_boundaries": active_boundaries if active_boundaries else planned_boundaries,
            "control_frequency_hz": self.control_frequency_hz,
            "available_tasks": available_tasks,
            "planned_task": planned_task,
            "planned_boundaries": planned_boundaries,
        }

    def start_task(self, name: str) -> None:
        """Legacy single-task activation helper; prefer start()."""
        self._runtime.start_task(name)
        self._active_task = name

    def step(self) -> Any:
        """Legacy single-cycle helper; the service must use start() instead."""
        return self._step()

    def _step(self) -> Any:
        return self._step_fn()

    def _run_loop(self, n_cycles: int, period_s: float) -> None:
        cycle = 0
        final_status = RunnerStatus.STOPPED
        try:
            while True:
                with self._lock:
                    status = self._status
                if status in (RunnerStatus.STOPPING, RunnerStatus.STOPPED, RunnerStatus.EMERGENCY):
                    break
                if status == RunnerStatus.PAUSED:
                    self._pause_event.wait(timeout=0.1)
                    continue

                t0 = time.perf_counter()
                try:
                    result = self._step()
                    fault_reason = self._fault_reason(result)
                    if fault_reason is not None:
                        with self._lock:
                            self._error = fault_reason
                            self._status = RunnerStatus.EMERGENCY
                        final_status = RunnerStatus.EMERGENCY
                        if self._on_fault is not None:
                            self._on_fault(fault_reason)
                        break

                    with self._lock:
                        self._cycle_count += 1
                    if self._on_cycle is not None:
                        self._on_cycle(result)
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001 — runner converts cycle failures to emergency
                    with self._lock:
                        self._error = str(exc)
                        self._status = RunnerStatus.EMERGENCY
                    final_status = RunnerStatus.EMERGENCY
                    if self._on_fault is not None:
                        self._on_fault(str(exc))
                    break

                cycle += 1
                if n_cycles != -1 and cycle >= n_cycles:
                    break

                sleep_s = period_s - (time.perf_counter() - t0)
                if sleep_s > 0:
                    time.sleep(sleep_s)

        finally:
            if final_status != RunnerStatus.EMERGENCY:
                with self._lock:
                    if self._status in (RunnerStatus.RUNNING, RunnerStatus.STOPPING):
                        self._status = RunnerStatus.STOPPED
                    final_status = self._status
            with contextlib.suppress(Exception):
                self._runtime.stop_task()
            if self._on_finished is not None:
                self._on_finished(final_status)

    @staticmethod
    def _fault_reason(result: Any) -> str | None:
        from dam.types.result import GuardDecision

        guard_results = getattr(result, "guard_results", None) or []
        for guard_result in guard_results:
            if getattr(guard_result, "decision", None) == GuardDecision.FAULT:
                return getattr(guard_result, "reason", None) or "Fault"
        return None


class SimulationRunner(RuntimeLoopRunner):
    """Execution strategy for simulated environments (Datasets, Mock robots)."""

    def __init__(
        self,
        runtime: GuardRuntime,
        control_frequency_hz: float = 10.0,
        frame_hub: Any | None = None,
        auxiliary_sources: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            runtime,
            control_frequency_hz=control_frequency_hz,
            frame_hub=frame_hub,
            auxiliary_sources=auxiliary_sources,
        )

    def connect(self) -> None:
        self._mark_connected()
        # Connect all sources
        for src in self._runtime._sources.values():
            if hasattr(src, "connect"):
                src.connect()
        for src in self._auxiliary_sources.values():
            if hasattr(src, "connect"):
                src.connect()

    def verify(self) -> None:
        # Verify all sources
        for src in self._runtime._sources.values():
            if hasattr(src, "verify"):
                src.verify()
        for src in self._auxiliary_sources.values():
            if hasattr(src, "verify"):
                src.verify()

    def shutdown(self) -> None:
        super().shutdown()
